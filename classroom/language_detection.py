"""Bounded recent-speech language probes, separate from Whisper transcription.

A single language token is a decoding hint, not a per-word language guarantee.
We re-probe recent unconfirmed speech instead of locking a whole class to its first language.
"""
class SpeechLanguageRouter:
    def __init__(self, detector):
        self.detector=detector
        self.reset()

    def reset(self):
        self.language=None
        self.last_end=-1e9
        self.last_info={}
        self.probe_cache={}
        self.decode_cache={}
        self.frontier=0

    @staticmethod
    def classify(probabilities, prior=None):
        # The detector distributes probability over ~100 languages. Its absolute
        # score is not transcription confidence within the user's three-language set.
        allowed={k:float(probabilities.get(k,0)) for k in ('en','ru','zh')}
        best=max(probabilities,key=probabilities.get) if probabilities else None
        candidate=max(allowed,key=allowed.get)
        mass=sum(allowed.values())
        relative=allowed[candidate]/mass if mass else 0
        strong=best in allowed and allowed[candidate]>=.35
        hint=candidate if strong or (mass>=.03 and relative>=.65) else None
        strategy='detector' if strong else 'supported_relative' if hint else 'await_context'
        if hint is None and prior in allowed and mass>=.03 and allowed[prior]>=.3*mass:
            hint=prior;strategy='stable_prior'
        return hint,dict(language_hint=hint,detected_language=best,
            language_probabilities=allowed,supported_probability=mass,
            relative_probability=relative,language_uncertain=not strong,
            hint_strategy=strategy)

    def choose(self, audio, offset):
        end=offset+len(audio)/16000
        if end-self.last_end < .8:
            return self.last_info.get("language_hint",self.language),self.last_info
        # Prefer still-unconfirmed audio, with at most 1 s of prior context.
        start=max(0,len(audio)-6*16000,round((self.frontier-offset-1)*16000))
        sample=audio[start:]
        if len(sample)<16000:
            return self.last_info.get("language_hint",self.language),self.last_info
        probabilities=self.detector(sample)
        self.last_end=end
        hint,info=self.classify(probabilities,self.language)
        if hint is not None and not info['language_uncertain']:
            self.language=hint
        self.last_info=dict(info,probe_start=offset+start/16000,probe_end=end)
        return hint,self.last_info


    def decode(self, model, audio, offset, **options):
        """Decode acoustically separated spans in their own language, then join times.

        This is not three competing transcripts of the same audio. Quiet boundaries
        partition the existing window; adjacent same-language spans are merged.
        """
        import numpy as np
        import hashlib
        size=len(audio)
        if not size:return {'segments':[], 'language':self.language}
        # 10 ms RMS frames; avoid fragmenting speech into subsecond guesses.
        # Quiet gaps >=120 ms need >=900 ms before and >=800 ms after them.
        cuts=[0];quiet=None
        for i in range(0,size,160):
            frame=audio[i:i+160]
            silent=float(np.sqrt(np.mean(frame*frame)))<.001
            if silent and quiet is None:quiet=i
            if not silent and quiet is not None:
                if i-quiet>=1920 and quiet-cuts[-1]>=14400 and size-i>=12800:
                    cuts.append((quiet+i)//2)
                quiet=None
        cuts.append(size)
        # Limit probes/decodes even in audio with many pauses. Existing 30 s limits stay.
        if len(cuts)>9:cuts=cuts[:8]+[size]
        regions=[];probes=[]
        for start,end in zip(cuts,cuts[1:]):
            key=(round(offset*16000)+start,round(offset*16000)+end)
            if len(cuts)==2:
                language,info=self.choose(audio,offset)
                probes.append(dict(start=offset+start/16000,end=offset+end/16000,**info))
                regions.append((start,end,language,info.get('language_uncertain',language is None)))
                break
            probabilities=self.probe_cache.get(key)
            if probabilities is None:
                probabilities=self.detector(audio[start:end]);self.probe_cache[key]=probabilities
            language,info=self.classify(probabilities,self.language)
            uncertain=info['language_uncertain']
            probes.append(dict(start=offset+start/16000,end=offset+end/16000,**info))
            if regions and regions[-1][2]==language:
                regions[-1]=(regions[-1][0],end,language,uncertain or regions[-1][3])
            else:regions.append((start,end,language,uncertain))
        # A weak short acoustic split is not a proven language boundary. Give
        # it the context of agreeing neighbours, or one clear adjacent language.
        # Merge BEFORE decoding so a cached 1 s fragment cannot fail forever.
        resolved=[]
        for i,(start,end,language,uncertain) in enumerate(regions):
            if language is None and (end-start)/16000<=2.5:
                left=regions[i-1][2] if i else None
                right=regions[i+1][2] if i+1<len(regions) else None
                neighbours={x for x in (left,right) if x in ('en','ru','zh')}
                if len(neighbours)==1:language=neighbours.pop()
            if resolved and language is not None and resolved[-1][2]==language:
                resolved[-1]=(resolved[-1][0],end,language,uncertain or resolved[-1][3])
            else:resolved.append((start,end,language,uncertain))
        regions=resolved
        if len(regions)>4:
            regions=regions[:3]+[(regions[3][0],size,None,True)]
        if len(self.probe_cache)>64:self.probe_cache=dict(list(self.probe_cache.items())[-32:])
        segments=[];windows=[];languages=[];quality_issue=None
        for start,end,language,uncertain in regions:
            # An uncertain supported hint may still yield a valid transcript.
            # Never use unrestricted auto-detection when no hint is available.
            if language not in ('en','ru','zh'):
                quality_issue=quality_issue or 'language_uncertain'
                windows.append(dict(start=offset+start/16000,end=offset+end/16000,
                    language=language,uncertain=True,cached=False,rejected='language_uncertain'))
                continue
            kwargs=dict(options,language=language,task='transcribe')
            # A language-specific history prompt must never spill into another span.
            kwargs['initial_prompt']=''
            sample=audio[start:end]
            key=(round(offset*16000)+start,round(offset*16000)+end,language,
                options.get('temperature',0),hashlib.blake2b(sample.tobytes(),digest_size=16).digest())
            hit=key in self.decode_cache
            if hit:result=self.decode_cache[key]
            else:
                result=model(sample,**kwargs)
                self.decode_cache[key]=result
            if len(self.decode_cache)>32:self.decode_cache=dict(list(self.decode_cache.items())[-16:])
            from .asr_quality import segments_issue
            quality_issue=quality_issue or segments_issue(result.get('segments',[]),(end-start)/16000)
            if result.get('language',language) not in ('en','ru','zh'):
                quality_issue=quality_issue or 'unsupported_language'
            import unicodedata
            text=' '.join(s.get('text','') for s in result.get('segments',[]))
            if any(c.isalpha() and not any(script in unicodedata.name(c,'')
                    for script in ('LATIN','CYRILLIC','CJK','IDEOGRAPH')) for c in text):
                quality_issue=quality_issue or 'unsupported_script'
            languages.append(result.get('language',language))
            for segment in result.get('segments',[]):
                shifted=dict(segment,start=segment['start']+start/16000,end=segment['end']+start/16000,id=len(segments))
                shifted['words']=[dict(w,start=w['start']+start/16000,end=w['end']+start/16000) for w in segment.get('words',[])]
                segments.append(shifted)
            windows.append(dict(start=offset+start/16000,end=offset+end/16000,language=result.get('language',language),uncertain=uncertain,cached=hit))
        # Keep the last confident acoustic probe, not the last merged region:
        # merging one weak fragment must not erase confident speech later in it.
        # Otherwise an old prefix can flip the prior on every growing decode,
        # preventing LocalAgreement from ever committing the same words twice.
        if not quality_issue:
            for probe in reversed(probes):
                if not probe['language_uncertain'] and probe['language_hint'] in ('en','ru','zh'):
                    self.language=probe['language_hint'];break
        return dict(segments=segments,language='mixed' if len(set(languages))>1 else self.language,
            _language_windows=windows,_language_probes=probes,_quality_issue=quality_issue)


def mlx_language_detector(model_path):
    # Called on the same dedicated MLX worker as every decoder; cached weights only.
    def detect(audio):
        import mlx.core as mx
        from mlx_whisper.audio import log_mel_spectrogram,pad_or_trim,N_FRAMES
        from mlx_whisper.transcribe import ModelHolder
        weights=ModelHolder.get_model(str(model_path),mx.float16)
        mel=pad_or_trim(log_mel_spectrogram(audio,n_mels=weights.dims.n_mels),N_FRAMES,axis=-2).astype(mx.float16)
        _,probabilities=weights.detect_language(mel)
        return probabilities
    return detect
