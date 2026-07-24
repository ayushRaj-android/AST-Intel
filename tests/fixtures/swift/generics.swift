class Container<T> {
    var value: T

    init(value: T) {
        self.value = value
    }

    func map<U>(transform: (T) -> U) -> Container<U> {
        return Container<U>(value: transform(value))
    }
}

protocol Comparable2 where Self: Equatable {
    func compare(other: Self) -> Int
}

struct Pair<A, B> {
    let first: A
    let second: B
}
