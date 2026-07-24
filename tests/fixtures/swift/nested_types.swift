class Outer {
    class Inner {
        var value: Int

        init(value: Int) {
            self.value = value
        }

        func describe() -> String {
            return "Inner(\(value))"
        }
    }

    struct InnerStruct {
        let id: Int
    }
}
