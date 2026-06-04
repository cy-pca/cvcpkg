#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "greet.h"

char *greet_formal(const char *name) {
    const char *prefix = "Good day, ";
    const char *suffix = ". How do you do?";
    size_t len = strlen(prefix) + strlen(name) + strlen(suffix) + 1;
    char *buf = malloc(len);
    if (!buf) return NULL;
    snprintf(buf, len, "%s%s%s", prefix, name, suffix);
    return buf;
}
