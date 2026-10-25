// Same Page app icon: waveform turning into subtitle lines, flat colours.
// Deterministic; no image generation or external fonts.
// Usage: swift generate.swift <output.png>
import AppKit

let side: CGFloat = 1024
// macOS icon grid: an 824 pt tile centred on the 1024 canvas.
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let unit = tile.width / 100   // the design is drawn on a 100 x 100 grid

func color(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: 1)
}
// A rounded bar given in design units with a top-left origin.
func bar(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSBezierPath {
    let rect = NSRect(x: tile.minX + x * unit, y: tile.maxY - (y + h) * unit, width: w * unit, height: h * unit)
    let r = min(rect.width, rect.height) / 2
    return NSBezierPath(roundedRect: rect, xRadius: r, yRadius: r)
}

let image = NSImage(size: NSSize(width: side, height: side))
image.lockFocus()
NSGraphicsContext.current?.imageInterpolation = .high

// Tile with the soft shadow macOS icons sit on.
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.shadowBlurRadius = 24
NSGraphicsContext.saveGraphicsState()
shadow.set()
color(0x173d34).setFill()
NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185).fill()
NSGraphicsContext.restoreGraphicsState()

// Waveform (mint).
color(0x6fd4ac).setFill()
for (x, y, h) in [(17.0, 43.0, 14.0), (27.0, 33.0, 34.0), (37.0, 24.0, 52.0), (47.0, 36.0, 28.0)] {
    bar(CGFloat(x), CGFloat(y), 6, CGFloat(h)).fill()
}
// Subtitle lines (cream).
color(0xf4f2ec).setFill()
for (y, w) in [(35.0, 25.0), (46.5, 19.0), (58.0, 23.0)] {
    bar(59, CGFloat(y), CGFloat(w), 7).fill()
}

image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
