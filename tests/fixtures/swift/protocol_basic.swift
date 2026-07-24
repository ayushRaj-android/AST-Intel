public protocol Drawable {
    func draw()
    func color() -> String
    associatedtype Element
}

protocol Resizable {
    func resize(width: Double, height: Double)
    func reset()
}
