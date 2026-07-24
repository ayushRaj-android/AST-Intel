protocol Serializable {}
protocol Logging {
    func log(msg: String)
}
protocol Renderable {
    func render()
}

class Service: Serializable, Logging, Renderable {
    func log(msg: String) {
        print(msg)
    }

    func render() {}
}

class Base {
    var id: Int

    init(id: Int) {
        self.id = id
    }
}

class Child: Base, Logging {
    var extra: String

    init(id: Int, extra: String) {
        self.extra = extra
        super.init(id: id)
    }

    func log(msg: String) {
        print("[\(id)] \(msg)")
    }
}
