#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hello.h"
#include "greet.h"

int main(void) {
    int failures = 0;

    /* Test hello library */
    char *h = hello_greet("World");
    if (!h) {
        fprintf(stderr, "FAIL: hello_greet returned NULL\n");
        return 1;
    }
    if (strcmp(h, "Hello, World!") != 0) {
        fprintf(stderr, "FAIL: hello_greet got '%s', expected 'Hello, World!'\n", h);
        failures++;
    } else {
        printf("PASS: hello_greet => '%s'\n", h);
    }
    free(h);

    /* Test greet library */
    char *g = greet_formal("Tester");
    if (!g) {
        fprintf(stderr, "FAIL: greet_formal returned NULL\n");
        return 1;
    }
    if (strcmp(g, "Good day, Tester. How do you do?") != 0) {
        fprintf(stderr, "FAIL: greet_formal got '%s', expected 'Good day, Tester. How do you do?'\n", g);
        failures++;
    } else {
        printf("PASS: greet_formal => '%s'\n", g);
    }
    free(g);

    if (failures > 0) {
        fprintf(stderr, "%d test(s) FAILED\n", failures);
        return 1;
    }
    printf("All tests PASSED\n");
    return 0;
}
