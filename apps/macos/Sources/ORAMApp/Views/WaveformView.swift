import SwiftUI

struct WaveformView: View {
    let samples: [Double]

    var body: some View {
        Canvas { context, size in
            let values = samples.isEmpty ? Array(repeating: 0.0, count: 64) : samples
            let width = size.width / CGFloat(max(values.count, 1))
            let midY = size.height / 2

            for index in values.indices {
                let value = max(0, min(1, values[index]))
                let height = max(1, CGFloat(value) * size.height)
                let rect = CGRect(
                    x: CGFloat(index) * width,
                    y: midY - height / 2,
                    width: max(1, width - 1),
                    height: height
                )
                context.fill(Path(roundedRect: rect, cornerRadius: 1), with: .color(.accentColor.opacity(0.65)))
            }
        }
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
    }
}
