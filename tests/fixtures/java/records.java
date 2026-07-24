public record Point(int x, int y) {}

public record UserDto(String name, int age, String email) {}

public record Config(String host, int port) {
    public String url() {
        return host + ":" + port;
    }
}
