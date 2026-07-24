/**
 * Fixture: JavaScript classes — tests StructNode and ImplBlockNode extraction.
 */

class Animal {
    constructor(name, sound) {
        this.name = name;
        this.sound = sound;
    }

    speak() {
        return this.sound;
    }

    static create(name) {
        return new Animal(name, "generic");
    }
}

class Dog extends Animal {
    constructor(name, breed) {
        super(name, "Woof");
        this.breed = breed;
    }

    fetch(item) {
        return item;
    }
}

module.exports = { Animal, Dog };
