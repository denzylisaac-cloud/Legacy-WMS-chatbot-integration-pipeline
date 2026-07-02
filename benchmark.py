import timeit
import re
import numpy as np

def original_text_to_vector(text, dimension=128):
    words = re.findall(r'\w+', text.lower())
    vec = np.zeros(dimension, dtype=np.float32)
    for word in words:
        h = 2166136261
        for char in word:
            h = h ^ ord(char)
            h = (h * 16777619) & 0xffffffff
        idx = h % dimension
        vec[idx] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

import zlib

def optimized_text_to_vector(text, dimension=128):
    words = re.findall(r'\w+', text.lower())
    vec = np.zeros(dimension, dtype=np.float32)
    for word in words:
        # Using zlib.crc32 which is much faster than manual character iteration in Python
        h = zlib.crc32(word.encode('utf-8'))
        idx = h % dimension
        vec[idx] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

text = "This is a sample text that we want to convert to a vector. We want it to be long enough to show some performance differences. Skus, names, locations, stock, safety stock, daily demand, lead time, weight, volume, moq, order cost, holding cost." * 100

t1 = timeit.timeit(lambda: original_text_to_vector(text), number=100)
t2 = timeit.timeit(lambda: optimized_text_to_vector(text), number=100)

print(f"Original: {t1:.4f} seconds")
print(f"Optimized: {t2:.4f} seconds")
