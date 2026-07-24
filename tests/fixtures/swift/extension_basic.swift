class Person {
    var name: String
    var age: Int

    init(name: String, age: Int) {
        self.name = name
        self.age = age
    }

    func greet() -> String {
        return "Hello, I'm \(name)"
    }
}

extension Person: CustomStringConvertible {
    var description: String {
        return "Person(\(name), \(age))"
    }
}

extension Person {
    func isAdult() -> Bool {
        return age >= 18
    }

    static func create(name: String) -> Person {
        return Person(name: name, age: 0)
    }
}
