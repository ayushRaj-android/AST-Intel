/**
 * Fixture: JavaScript functions — tests FunctionNode extraction.
 */

async function fetchData(url) {
    return fetch(url);
}

function add(a, b) {
    return a + b;
}

const handler = async (req) => {};
const multiply = (x, y) => x * y;
const log = (msg) => {
    console.log(msg);
};

// Default export
export default function defaultFn() {
    return "default";
}

export { fetchData, add };
