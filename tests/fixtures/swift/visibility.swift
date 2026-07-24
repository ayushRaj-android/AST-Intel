public class VisibilityDemo {
    public var publicVar: Int = 1
    private var privateVar: Int = 2
    internal var internalVar: Int = 3
    fileprivate var fileprivateVar: Int = 4

    public func publicMethod() {}
    private func privateMethod() {}
    internal func internalMethod() {}
    fileprivate func fileprivateMethod() {}
}
