"""Quasi: what the tools used random.Random for, computed instead of drawn. Every value comes from a Halton sequence
(one prime base per call site in turn), so the same seed always gives the same values, and they spread evenly over
their range instead of clustering. Same methods as random.Random: random, uniform, randint, randrange, choice, choices,
gauss, shuffle, getrandbits, betavariate.

    q = Quasi(seed)
    q.choice(names); q.randint(1, 4); q.gauss(0, 2048)
"""
import math

_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53)


def halton(i, base):
    f, r = 1.0, 0.0
    while i > 0:
        f /= base
        r += f * (i % base)
        i //= base
    return r


class Quasi:
    def __init__(self, seed=0):
        self.i = 1 + abs(int(seed)) % 1000003
        self.k = 0

    def random(self):
        """the next value in [0, 1): the counter's Halton value in the next base"""
        b = _PRIMES[self.k % len(_PRIMES)]
        self.k += 1
        if self.k % len(_PRIMES) == 0:
            self.i += 1
        return halton(self.i, b)

    def uniform(self, a, b):
        return a + (b - a) * self.random()

    def randint(self, a, b):
        return a + min(b - a, int(self.random() * (b - a + 1)))

    def randrange(self, start, stop=None, step=1):
        if stop is None:
            start, stop = 0, start
        n = max(1, (stop - start + step - 1) // step)
        return start + step * min(n - 1, int(self.random() * n))

    def choice(self, seq):
        return seq[min(len(seq) - 1, int(self.random() * len(seq)))]

    def choices(self, population, weights=None, k=1):
        if weights is None:
            return [self.choice(population) for _ in range(k)]
        tot = float(sum(weights))
        out = []
        for _ in range(k):
            r, acc = self.random() * tot, 0.0
            pick = population[-1]
            for p, w in zip(population, weights):
                acc += w
                if r < acc:
                    pick = p
                    break
            out.append(pick)
        return out

    def gauss(self, mu=0.0, sigma=1.0):
        """the normal quantile of the next value (inverse CDF, Acklam's approximation)"""
        p = min(max(self.random(), 1e-9), 1 - 1e-9)
        a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
             -3.066479806614716e+01, 2.506628277459239e+00)
        b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
             -1.328068155288572e+01)
        c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
             4.374664141464968e+00, 2.938163982698783e+00)
        d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
        if p < 0.02425:
            q = math.sqrt(-2 * math.log(p))
            x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        elif p > 1 - 0.02425:
            q = math.sqrt(-2 * math.log(1 - p))
            x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        else:
            q = p - 0.5
            r = q * q
            x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
                (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        return mu + sigma * x

    def betavariate(self, alpha, beta):
        """the beta law's mean (no draw)"""
        return alpha / (alpha + beta)

    def getrandbits(self, k):
        return int(self.random() * (1 << k))

    def shuffle(self, x):
        """a spread order (van der Corput on the index), the same for the same list length"""
        n = len(x)
        order = sorted(range(n), key=lambda i: (halton(i + 1, 2), i))
        x[:] = [x[i] for i in order]
