// prime_bug3.java

import java.util.Scanner;

public class prime_bug3 {

    public static boolean isPrime(int n) {
        if (n <= 1) return false;

        for (int i = 2; i * i <= n; i++) {
            if (n % i == 0)
                return false;
        }
        return true;
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.print("Enter a number: ");
        int num = sc.nextInt();

        if (isPrim(num))
            System.out.println(num + " is prime");
        else
            System.out.println(num + " is not prime");

        sc.close();
    }
}