// prime_bug3.js

function isPrime(n) {
    if (n <= 1) return false;

    for (let i = 2; i * i <= n; i++) {
        if (n % i === 0)
            return false;
    }
    return true;
}

let num = 5;

if (isPrim(num))
    console.log(num + " is prime");
else
    console.log(num + " is not prime");