// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0
import Foundation
import Testing
@testable import FrameInput

@Test func rgb565BigEndianPrimaryColorsBecomeBgra() throws {
    var frame = Data(repeating: 0, count: RGB565Frame.byteCount)
    frame.replaceSubrange(0..<10, with: [0xf8, 0x00, 0x07, 0xe0, 0x00, 0x1f, 0xff, 0xff, 0x00, 0x00])
    let pixels = try RGB565Frame.bgra(frame)
    #expect(Array(pixels.prefix(20)) == [
        0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255,
        255, 255, 255, 255, 0, 0, 0, 255,
    ])
}

@Test func partialReadsProduceExactlyOneFrameWithoutEatingTheNext() throws {
    let source = Data(repeating: 0x5a, count: RGB565Frame.byteCount * 2)
    var offset = 0
    func chunk(_ requested: Int) -> Data? {
        let count = min(requested, 113, source.count - offset)
        defer { offset += count }
        return source.subdata(in: offset..<(offset + count))
    }
    #expect(try RGB565Frame.read(using: chunk)?.count == RGB565Frame.byteCount)
    #expect(offset == RGB565Frame.byteCount)
    #expect(try RGB565Frame.read(using: chunk)?.count == RGB565Frame.byteCount)
    #expect(try RGB565Frame.read(using: chunk) == nil)
}

@Test func truncatedRecordsAndWrongPixelSizesAreRejected() {
    var first = true
    #expect(throws: FrameInputError.self) {
        try RGB565Frame.read { _ in
            defer { first = false }
            return first ? Data([0, 1]) : nil
        }
    }
    #expect(throws: FrameInputError.self) { try RGB565Frame.bgra(Data([0, 1])) }
}
