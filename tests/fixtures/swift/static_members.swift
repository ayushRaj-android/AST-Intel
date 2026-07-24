class StaticMembers {
    static func create() -> StaticMembers {
        return StaticMembers()
    }

    class func override_me() -> String {
        return "base"
    }

    static let shared = StaticMembers()
}
