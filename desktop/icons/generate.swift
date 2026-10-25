// Deterministic SP app icon; no image generation or external fonts.
import AppKit
let side: CGFloat = 1024
let image = NSImage(size: NSSize(width: side, height: side))
image.lockFocus()
NSColor(calibratedRed: 0.122, green: 0.435, blue: 0.361, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: side, height: side), xRadius: 224, yRadius: 224).fill()
let text = "SP" as NSString
let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 430, weight: .semibold), .foregroundColor: NSColor.white, .kern: -20]
let measured = text.size(withAttributes: attributes)
text.draw(at: NSPoint(x: (side-measured.width)/2, y: (side-measured.height)/2+14), withAttributes: attributes)
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
