cmake_minimum_required(VERSION 3.20)
project(safeguard_core VERSION 1.2.3)

find_package(OpenSSL REQUIRED)
find_package(Boost 1.80 COMPONENTS filesystem system)
find_package(fmt CONFIG REQUIRED)

add_library(core
    src/storage.cpp
    src/worker.cpp
)

target_link_libraries(core
    OpenSSL::SSL
    Boost::filesystem
    fmt::fmt
)
