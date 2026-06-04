#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hello.h"

char *hello_greet(const char *name) {
    const char *prefix = "Hello, ";
    const char *suffix = "!";
    size_t len = strlen(prefix) + strlen(name) + strlen(suffix) + 1;
    char *buf = malloc(len);
    if (!buf) return NULL;
    snprintf(buf, len, "%s%s%s", prefix, name, suffix);
    return buf;
}
