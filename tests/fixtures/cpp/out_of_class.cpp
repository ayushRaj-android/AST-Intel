namespace project::core {

class Worker {
public:
    void handle(int event);
    bool is_ready() const;
private:
    bool ready_;
};

// Out-of-class method definitions.
void Worker::handle(int event) {
    ready_ = true;
}

bool Worker::is_ready() const {
    return ready_;
}

}  // namespace project::core
