import Foundation

/// A basic user class for testing.
public class User {
    var id: UUID
    var name: String
    private var email: String

    init(id: UUID, name: String, email: String) {
        self.id = id
        self.name = name
        self.email = email
    }

    func greet() -> String {
        return "Hello, \(name)"
    }

    private func validate() -> Bool {
        return !email.isEmpty
    }

    override func toString() -> String {
        return "User(\(name))"
    }
}
