import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: path)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    print("FILE\t\(path)")
    for observation in request.results ?? [] {
        if let best = observation.topCandidates(1).first {
            print("\(best.string)\t\(best.confidence)\t\(observation.boundingBox)")
        }
    }
}
