/// A 2D point as a struct.
public struct Point {
    var x: Double
    var y: Double

    func distance(to other: Point) -> Double {
        let dx = x - other.x
        let dy = y - other.y
        return (dx * dx + dy * dy).squareRoot()
    }

    mutating func translate(dx: Double, dy: Double) {
        x += dx
        y += dy
    }
}

struct Config {
    let host: String
    let port: Int
    var debug: Bool = false
}
