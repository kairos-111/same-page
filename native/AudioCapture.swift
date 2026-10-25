import Foundation
import AVFoundation
import ScreenCaptureKit
import CoreMedia
import AppKit

func status(_ message: String, _ kind: String = "status") {
    let obj: [String: String] = ["type": kind, "message": message]
    if let data = try? JSONSerialization.data(withJSONObject: obj) {
        try? FileHandle.standardError.write(contentsOf: data + Data([10]))
    }
}

final class PCMWriter {
    let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    var converter: AVAudioConverter?
    var previous: AVAudioFormat?
    let lock = NSLock()
    func send(_ input: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        if previous != input.format {
            converter = AVAudioConverter(from: input.format, to: target)
            previous = input.format
        }
        guard let converter = converter else { return }
        let count = AVAudioFrameCount(ceil(Double(input.frameLength) * 16000 / input.format.sampleRate)) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: count) else { return }
        var supplied = false
        var error: NSError?
        let result = converter.convert(to: output, error: &error) { _, inputStatus in
            if supplied { inputStatus.pointee = .noDataNow; return nil }
            supplied = true; inputStatus.pointee = .haveData; return input
        }
        if result == .error { status(error?.localizedDescription ?? "音频格式转换失败", "error"); return }
        guard output.frameLength > 0, let pointer = output.int16ChannelData?[0] else { return }
        do { try FileHandle.standardOutput.write(contentsOf: Data(bytes: pointer, count: Int(output.frameLength) * 2)) }
        catch { exit(0) }
    }
}

final class SystemCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    let writer = PCMWriter()
    var stream: SCStream?
    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) ?? content.displays.first else {
            throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "未找到可采集的显示器"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.width = 32; config.height = 32
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: DispatchQueue(label: "classroom.system-audio"))
        stream = s
        try await s.startCapture()
        status("系统音频采集已开始", "ready")
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        status(error.localizedDescription, "error"); exit(2)
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid,
              let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              let format = AVAudioFormat(streamDescription: asbd) else { return }
        var size = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sampleBuffer, bufferListSizeNeededOut: &size, bufferListOut: nil,
            bufferListSize: 0, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
        let memory = UnsafeMutableRawPointer.allocate(byteCount: max(size, MemoryLayout<AudioBufferList>.size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        let list = memory.assumingMemoryBound(to: AudioBufferList.self)
        var block: CMBlockBuffer?
        let result = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sampleBuffer, bufferListSizeNeededOut: nil,
            bufferListOut: list, bufferListSize: size, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: &block)
        guard result == noErr,
              let pcm = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: list, deallocator: nil) else { return }
        pcm.frameLength = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        writer.send(pcm)
        withExtendedLifetime(block) {}
    }
}

let arguments = CommandLine.arguments
let mode = arguments.dropFirst().first ?? "--check"
if mode == "--check" {
    let mic = AVCaptureDevice.authorizationStatus(for: .audio).rawValue
    print("{\"platform\":\"macOS\",\"output\":\"pcm_s16le_16000_mono\",\"microphone_authorization\":\(mic),\"screen_authorization\":\(CGPreflightScreenCaptureAccess())}")
    exit(0)
}
guard mode == "microphone" || mode == "system" else { status("未知音源", "error"); exit(2) }
signal(SIGPIPE, SIG_IGN)
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
var engine: AVAudioEngine?
var capture: SystemCapture?
let writer = PCMWriter()
Task { @MainActor in
    do {
        if mode == "microphone" {
            let allowed = await AVCaptureDevice.requestAccess(for: .audio)
            guard allowed else { status("麦克风权限未授予，请在系统设置中允许 Same Page 音频采集使用麦克风", "error"); exit(3) }
            let e = AVAudioEngine()
            let input = e.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0 && format.channelCount > 0 else { status("默认麦克风不可用", "error"); exit(4) }
            input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in writer.send(buffer) }
            try e.start(); engine = e
            status("麦克风采集已开始", "ready")
        } else {
            let c = SystemCapture(); capture = c; try await c.start()
        }
    } catch { status(error.localizedDescription, "error"); exit(2) }
}
RunLoop.main.run()
