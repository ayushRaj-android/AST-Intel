#include <string>
#include <vector>
#include "handler.h"

namespace project::core {

// A storage class with access specifiers.
class Storage {
public:
    void store(const std::string& key, int value);
    int retrieve(const std::string& key) const;
    static Storage& instance();

private:
    int data_;
    std::string name_;

protected:
    void internal_cleanup();
};

// Pure virtual interface → should become TraitNode.
class IHandler {
public:
    virtual void handle(int event) = 0;
    virtual bool is_ready() const = 0;
    virtual ~IHandler() = default;
};

// Inherits from IHandler → should produce ImplBlockNode.
class Worker : public IHandler {
public:
    void handle(int event) override;
    bool is_ready() const override;

    void set_name(const std::string& name);

private:
    std::string name_;
    bool ready_ = false;
};

// Template class.
template<typename T>
class Container {
public:
    void add(const T& item);
    T get(int index) const;
    int size() const;

private:
    std::vector<T> items_;
};

// Template with multiple params.
template<typename K, typename V>
class Pair {
public:
    K first;
    V second;
};

// Enum class (scoped).
enum class Color {
    Red,
    Green,
    Blue
};

// Old-style enum.
enum Direction {
    North,
    South,
    East,
    West
};

}  // namespace project::core
