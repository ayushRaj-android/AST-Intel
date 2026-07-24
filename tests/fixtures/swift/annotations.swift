@available(iOS 15, *)
class OldClass {
    @discardableResult
    func fastMethod() -> Int {
        return 42
    }
}

@objc
class ObjCClass {
    @objc func doWork() {}
}
