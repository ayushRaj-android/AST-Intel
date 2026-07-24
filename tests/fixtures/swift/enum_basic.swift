public enum Direction {
    case north
    case south
    case east
    case west

    func opposite() -> Direction {
        switch self {
        case .north: return .south
        case .south: return .north
        case .east: return .west
        case .west: return .east
        }
    }
}

enum Result<T> {
    case success(T)
    case failure(Error)
}

enum Planet: Double {
    case mercury = 3.303e+23
    case venus = 4.869e+24
    case earth = 5.976e+24
}
