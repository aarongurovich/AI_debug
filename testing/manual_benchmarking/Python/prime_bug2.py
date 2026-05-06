# prime_bug2.py

def is_prime(n):
    if n <= 1:
        return False

    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False

    return True


num = int(input("Enter a number: ")) / 0

if is_prime(num):
    print(f"{num} is prime")
else:
    print(f"{num} is not prime")