/* winload: loads the game's Windows DLLs (PE32+ x86-64) into a Linux process and calls them, bit for bit as on Windows.

Why: the game DLLs (sm64_us.dll / sm64_jp.dll, the Wafel libsm64 builds, and the small romhack/rh_*.dll patches) are
shipped as Windows binaries and the tools patch their machine code by RVA (st_patches.py, st_romhack.py). Loading the very
same bytes keeps every frame exact and every RVA, patch site and symbol cache valid; a Linux rebuild would not.

What it does (winload.py is the Python side, a ctypes.CDLL look-alike):
  - maps the image (preferred base when free), applies the base relocations, sets each section's protection;
  - resolves the imports to the shims below (ms_abi C functions). Only exact functions are shimmed: memory and strings,
    the allocator, sqrt / floor / fmod (exact in every libm), stdio for messages, the kernel32 calls a DLL makes outside
    its CRT start. Anything else (sin, pow, fibers, ...) is bound to a trap that fails the call with the import's name:
    a result that could differ from Windows never comes back silently;
  - never runs DllMain: the game's code needs no CRT start (no constructors, no TLS, no pseudo relocations in these DLLs);
  - calls an export with the Windows x64 convention (wl_call): 32-byte shadow space, args in rcx/rdx/r8/r9 and xmm0-3
    (each slot loaded in both, the callee reads the one it declares), the rest on the stack; returns rax and xmm0;
  - runs every call with Windows' FPU state: x87 control word 0x27F (53-bit precision; Linux starts at 0x37F, 64-bit)
    and MXCSR 0x1F80. Some game functions (cur_obj_apply_drag_xz, the mingw sqrtf/sin/cos) use the x87 unit: without
    this their results would differ in the last bit;
  - a fault inside a call (access violation, divide by zero, illegal instruction, a trap, abort, an assert) unwinds back
    to wl_call, which reports it; winload.py raises OSError('exception: access violation ...') as ctypes does on Windows;
  - gives each calling thread a small TEB at GS (Self, stack bounds, a zeroed PEB) for code that reads gs:0x30.

Build: winload.py compiles this file on first use (cc -O2 -shared -fPIC) into _build/; no other dependency. */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <malloc.h>
#include <math.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <asm/prctl.h>

#define MS __attribute__((ms_abi))
#define EXPORT __attribute__((visibility("default")))

/* ------------------------------------------------------------------------------------------------------------------ */
/* faults: every call runs under a sigsetjmp; a fault, a trap or an abort longjmps back with a reason                 */

enum { WL_OK = 0, WL_SIGNAL = 1, WL_TRAP = 2 };

typedef struct {
    sigjmp_buf jb;
    int active;
    int kind;                 /* WL_SIGNAL / WL_TRAP */
    int sig, code;
    uintptr_t addr, rip;
    char msg[512];
} wl_ctx;

static __thread wl_ctx *t_ctx;
static __thread void *t_altstack;
static __thread void *t_teb;
static struct sigaction g_prev[65];
static pthread_once_t g_sig_once = PTHREAD_ONCE_INIT;

static void wl_fail(int kind, const char *fmt, ...) __attribute__((noreturn, format(printf, 2, 3)));

static void wl_fail(int kind, const char *fmt, ...) {
    wl_ctx *c = t_ctx;
    if (!c || !c->active) {
        va_list ap;
        va_start(ap, fmt);
        fprintf(stderr, "winload: fatal outside a call: ");
        vfprintf(stderr, fmt, ap);
        fputc('\n', stderr);
        va_end(ap);
        abort();
    }
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(c->msg, sizeof c->msg, fmt, ap);
    va_end(ap);
    c->kind = kind;
    siglongjmp(c->jb, 1);
}

static void wl_on_signal(int sig, siginfo_t *si, void *uc_) {
    wl_ctx *c = t_ctx;
    if (c && c->active) {
        ucontext_t *uc = uc_;
        c->kind = WL_SIGNAL;
        c->sig = sig;
        c->code = si->si_code;
        c->addr = (uintptr_t)si->si_addr;
        c->rip = (uintptr_t)uc->uc_mcontext.gregs[REG_RIP];
        c->msg[0] = 0;
        siglongjmp(c->jb, 1);
    }
    /* not ours: hand it to whoever had it (Python's faulthandler, the default action) */
    struct sigaction *p = &g_prev[sig];
    if (p->sa_flags & SA_SIGINFO) {
        if (p->sa_sigaction) {
            p->sa_sigaction(sig, si, uc_);
            return;
        }
    } else if (p->sa_handler != SIG_IGN && p->sa_handler != SIG_DFL && p->sa_handler) {
        p->sa_handler(sig);
        return;
    }
    signal(sig, SIG_DFL);           /* the fault happens again on return, now with the default action */
}

static void wl_install_signals(void) {
    int sigs[] = {SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGTRAP};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) {
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_sigaction = wl_on_signal;
        sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
        sigemptyset(&sa.sa_mask);
        sigaction(sigs[i], &sa, &g_prev[sigs[i]]);
    }
}

/* a fake TEB at GS for the calling thread (gs:0x30 Self, 0x08/0x10 stack base/limit, 0x60 PEB) */
static void wl_thread_setup(void) {
    if (!t_altstack) {
        size_t n = 256 * 1024;
        void *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p != MAP_FAILED) {
            stack_t ss = {.ss_sp = p, .ss_size = n, .ss_flags = 0};
            sigaltstack(&ss, NULL);
            t_altstack = p;
        }
    }
    if (!t_teb) {
        unsigned long cur = 0;
        syscall(SYS_arch_prctl, ARCH_GET_GS, &cur);
        if (cur)
            return;                 /* someone else owns GS on this thread: leave it */
        uint8_t *teb = calloc(1, 0x2000);
        uint8_t *peb = teb + 0x1000;
        pthread_attr_t at;
        void *lo = NULL;
        size_t sz = 0;
        if (pthread_getattr_np(pthread_self(), &at) == 0) {
            pthread_attr_getstack(&at, &lo, &sz);
            pthread_attr_destroy(&at);
        }
        *(uint64_t *)(teb + 0x08) = (uint64_t)((uint8_t *)lo + sz);
        *(uint64_t *)(teb + 0x10) = (uint64_t)lo;
        *(uint64_t *)(teb + 0x30) = (uint64_t)teb;
        *(uint64_t *)(teb + 0x40) = (uint64_t)getpid();
        *(uint64_t *)(teb + 0x48) = (uint64_t)syscall(SYS_gettid);
        *(uint64_t *)(teb + 0x60) = (uint64_t)peb;
        if (syscall(SYS_arch_prctl, ARCH_SET_GS, teb) == 0)
            t_teb = teb;
        else
            free(teb);
    }
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* the call bridge: SysV -> Windows x64                                                                               */

/* uint64_t wl_call_ms(void *fn, long nslots, const uint64_t *slots, uint64_t *xmm0_out)
   slots has at least 4 entries; slot i is loaded in the i-th integer register and in xmm i (i < 4), or the stack. */
__asm__(
    ".text\n"
    ".p2align 4\n"
    ".type wl_call_ms,@function\n"
    "wl_call_ms:\n"
    "  push %rbp\n"
    "  mov %rsp, %rbp\n"
    "  push %rbx\n"
    "  push %r12\n"
    "  push %r13\n"
    "  push %r14\n"
    "  push %r15\n"
    "  mov %rdi, %r12\n"            /* fn */
    "  mov %rsi, %r14\n"            /* n */
    "  mov %rdx, %r15\n"            /* slots */
    "  mov %rcx, %r13\n"            /* xmm0 out */
    "  mov %r14, %rax\n"
    "  sub $4, %rax\n"
    "  jg 1f\n"
    "  xor %eax, %eax\n"
    "1:\n"
    "  lea 32(,%rax,8), %rax\n"     /* shadow space + stack args */
    "  sub %rax, %rsp\n"
    "  and $-16, %rsp\n"
    "  mov $4, %rbx\n"
    "2:\n"
    "  cmp %r14, %rbx\n"
    "  jge 3f\n"
    "  mov (%r15,%rbx,8), %rax\n"
    "  mov %rax, (%rsp,%rbx,8)\n"   /* arg i at rsp + 8*i (i >= 4): after the 32-byte shadow space */
    "  inc %rbx\n"
    "  jmp 2b\n"
    "3:\n"
    "  mov 0(%r15), %rcx\n"
    "  movq %rcx, %xmm0\n"
    "  mov 8(%r15), %rdx\n"
    "  movq %rdx, %xmm1\n"
    "  mov 16(%r15), %r8\n"
    "  movq %r8, %xmm2\n"
    "  mov 24(%r15), %r9\n"
    "  movq %r9, %xmm3\n"
    "  call *%r12\n"
    "  movq %xmm0, (%r13)\n"
    "  lea -40(%rbp), %rsp\n"
    "  pop %r15\n"
    "  pop %r14\n"
    "  pop %r13\n"
    "  pop %r12\n"
    "  pop %rbx\n"
    "  pop %rbp\n"
    "  ret\n"
    ".size wl_call_ms, .-wl_call_ms\n");

uint64_t wl_call_ms(void *fn, long n, const uint64_t *slots, uint64_t *xmm0_out);

typedef struct {
    int kind, sig, code;
    uint64_t addr, rip;
    char msg[512];
} wl_fault;

/* Calls fn with the Windows convention under Windows' FPU state. Returns 0 (rax in *ret, xmm0 bits in *xret) or the
   fault kind (details in *f). nslots <= 64; slots must hold max(nslots, 4) entries. */
EXPORT int wl_call(void *fn, long nslots, const uint64_t *slots, uint64_t *ret, uint64_t *xret, wl_fault *f) {
    pthread_once(&g_sig_once, wl_install_signals);
    wl_thread_setup();
    uint64_t pad[4] = {0, 0, 0, 0};
    const uint64_t *use = slots;
    if (nslots < 4) {
        for (long i = 0; i < nslots; i++)
            pad[i] = slots[i];
        use = pad;
    }
    wl_ctx ctx;
    wl_ctx *outer = t_ctx;
    uint16_t cw_saved, cw_win = 0x27F;
    uint32_t mx_saved, mx_win = 0x1F80;
    __asm__ volatile("fnstcw %0" : "=m"(cw_saved));
    __asm__ volatile("stmxcsr %0" : "=m"(mx_saved));
    ctx.active = 1;
    t_ctx = &ctx;
    int rc = WL_OK;
    if (sigsetjmp(ctx.jb, 0) == 0) {           /* no mask to save: the handlers run with SA_NODEFER */
        __asm__ volatile("fninit; fldcw %0" : : "m"(cw_win));
        __asm__ volatile("ldmxcsr %0" : : "m"(mx_win));
        uint64_t x = 0;
        uint64_t r = wl_call_ms(fn, nslots, use, &x);
        *ret = r;
        *xret = x;
    } else {
        rc = ctx.kind;
        if (f) {
            f->kind = ctx.kind;
            f->sig = ctx.sig;
            f->code = ctx.code;
            f->addr = ctx.addr;
            f->rip = ctx.rip;
            memcpy(f->msg, ctx.msg, sizeof f->msg);
        }
        *ret = 0;
        *xret = 0;
    }
    __asm__ volatile("fninit; fldcw %0" : : "m"(cw_saved));
    __asm__ volatile("ldmxcsr %0" : : "m"(mx_saved));
    t_ctx = outer;
    return rc;
}

/* wl_call without arguments (sm64_update every frame): the result in *ret, 0 or the fault kind */
EXPORT int wl_call0(void *fn, uint64_t *ret, wl_fault *f) {
    uint64_t x;
    static const uint64_t none[4] = {0, 0, 0, 0};
    return wl_call(fn, 0, none, ret, &x, f);
}

/* returns its argument: lets ctypes turn byref(), arrays, bytes ... into an address for winload.py */
EXPORT uintptr_t wl_addr(uintptr_t p) { return p; }

/* ------------------------------------------------------------------------------------------------------------------ */
/* the loaded images                                                                                                  */

typedef struct wl_export { const char *name; uint32_t rva; } wl_export;

typedef struct wl_module {
    uint8_t *base;
    size_t size;
    char path[1024];
    char name[256];                 /* file name, lower case */
    wl_export *exports;
    uint32_t nexports;
    uint8_t *stubs;                 /* the traps of the unresolved imports */
    size_t stubs_size;
    struct wl_module *next;
} wl_module;

static pthread_mutex_t g_mods_lock = PTHREAD_MUTEX_INITIALIZER;
static wl_module *g_mods;

static const char *g_unresolved[8192];   /* names of the trapped imports (index in the trap stub) */
static int g_nunresolved;
static pthread_mutex_t g_unres_lock = PTHREAD_MUTEX_INITIALIZER;

static MS void wl_unimplemented(uint64_t idx) {
    const char *n = idx < (uint64_t)g_nunresolved ? g_unresolved[idx] : "?";
    wl_fail(WL_TRAP, "winload: %s is not available on Linux (no exact equivalent shimmed)", n);
}

static wl_module *wl_find_module_by_addr(uintptr_t a) {
    wl_module *r = NULL;
    pthread_mutex_lock(&g_mods_lock);
    for (wl_module *m = g_mods; m; m = m->next)
        if (a >= (uintptr_t)m->base && a < (uintptr_t)m->base + m->size) {
            r = m;
            break;
        }
    pthread_mutex_unlock(&g_mods_lock);
    return r;
}

static int cmp_export(const void *a, const void *b) {
    return strcmp(((const wl_export *)a)->name, ((const wl_export *)b)->name);
}

static void *wl_module_sym(wl_module *m, const char *name) {
    wl_export key = {name, 0};
    wl_export *e = bsearch(&key, m->exports, m->nexports, sizeof *e, cmp_export);
    return e ? m->base + e->rva : NULL;
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* shims: the C runtime (msvcrt, ucrt api sets, vcruntime)                                                            */

static __thread int t_errno_ms;
static __thread uint32_t t_last_error;

/* An MS shim never touches a __thread variable itself, only through these SysV functions. In this shared object a
   __thread access is a call to __tls_get_addr (global-dynamic model) that GCC does not count as a call: an ms_abi
   function neither saves rdi / rsi / xmm6-15 around it (kept by the callee on Windows, scratch for __tls_get_addr) nor
   keeps rdi out of it (the TLS descriptor is loaded there). The DLL then found its registers changed: mingw's printf
   keeps its flags in edi across _errno(), which left them holding an address of this library, so sprintf wrote to a
   FILE or nowhere depending on where ASLR put us (#21). A real call to a SysV function makes the ms_abi caller save
   everything Windows expects kept; noipa stops GCC from learning that the callee clobbers less. */
#if defined(__clang__)
#define WL_TLS_ACCESSOR __attribute__((noinline))
#else
#define WL_TLS_ACCESSOR __attribute__((noinline, noipa))
#endif
static WL_TLS_ACCESSOR int *tls_errno(void) { return &t_errno_ms; }
static WL_TLS_ACCESSOR uint32_t *tls_last_error(void) { return &t_last_error; }

static MS void *s_memcpy(void *d, const void *s, size_t n) { return memcpy(d, s, n); }
static MS void *s_memmove(void *d, const void *s, size_t n) { return memmove(d, s, n); }
static MS void *s_memset(void *d, int c, size_t n) { return memset(d, c, n); }
static MS int s_memcmp(const void *a, const void *b, size_t n) { return memcmp(a, b, n); }
static MS void *s_memchr(const void *s, int c, size_t n) { return (void *)memchr(s, c, n); }
static MS size_t s_strlen(const char *s) { return strlen(s); }
static MS size_t s_strnlen(const char *s, size_t n) { return strnlen(s, n); }
static MS int s_strcmp(const char *a, const char *b) { return strcmp(a, b); }
static MS int s_strncmp(const char *a, const char *b, size_t n) { return strncmp(a, b, n); }
static MS int s_stricmp(const char *a, const char *b) { return strcasecmp(a, b); }
static MS int s_strnicmp(const char *a, const char *b, size_t n) { return strncasecmp(a, b, n); }
static MS char *s_strcpy(char *d, const char *s) { return strcpy(d, s); }
static MS char *s_strncpy(char *d, const char *s, size_t n) { return strncpy(d, s, n); }
static MS char *s_strcat(char *d, const char *s) { return strcat(d, s); }
static MS char *s_strncat(char *d, const char *s, size_t n) { return strncat(d, s, n); }
static MS char *s_strchr(const char *s, int c) { return (char *)strchr(s, c); }
static MS char *s_strrchr(const char *s, int c) { return (char *)strrchr(s, c); }
static MS char *s_strstr(const char *s, const char *t) { return (char *)strstr(s, t); }
static MS char *s_strdup(const char *s) { return strdup(s); }
static MS size_t s_wcslen(const uint16_t *s) { size_t n = 0; while (s[n]) n++; return n; }
static MS void *s_malloc(size_t n) { return malloc(n); }
static MS void *s_calloc(size_t a, size_t b) { return calloc(a, b); }
static MS void *s_realloc(void *p, size_t n) { return realloc(p, n); }
static MS void s_free(void *p) { free(p); }
static MS size_t s_msize(void *p) { return malloc_usable_size(p); }
static MS void *s_aligned_malloc(size_t n, size_t al) {
    void *p = NULL;
    if (al < sizeof(void *)) al = sizeof(void *);
    return posix_memalign(&p, al, n ? n : 1) == 0 ? p : NULL;
}
static MS void s_aligned_free(void *p) { free(p); }
static MS int *s_errno(void) { return tls_errno(); }
static MS int s_abs(int x) { return x < 0 ? -x : x; }
static MS long long s_llabs(long long x) { return x < 0 ? -x : x; }
static MS int s_atoi(const char *s) { return atoi(s); }
static MS int32_t s_atol(const char *s) { return (int32_t)atol(s); }
static MS int32_t s_strtol(const char *s, char **e, int b) { return (int32_t)strtol(s, e, b); }
static MS uint32_t s_strtoul(const char *s, char **e, int b) { return (uint32_t)strtoul(s, e, b); }
static MS long long s_strtoll(const char *s, char **e, int b) { return strtoll(s, e, b); }
static MS unsigned long long s_strtoull(const char *s, char **e, int b) { return strtoull(s, e, b); }
static MS double s_strtod(const char *s, char **e) { return strtod(s, e); }      /* correctly rounded */
static MS double s_atof(const char *s) { return strtod(s, NULL); }
static MS char *s_getenv(const char *n) { return getenv(n); }
static MS char *s_ultoa(uint32_t v, char *buf, int radix) {
    char tmp[40];
    int i = 0;
    if (radix < 2 || radix > 36) radix = 10;
    do { int d = v % radix; tmp[i++] = (char)(d < 10 ? '0' + d : 'a' + d - 10); v /= radix; } while (v);
    for (int j = 0; j < i; j++) buf[j] = tmp[i - 1 - j];
    buf[i] = 0;
    return buf;
}

/* exact in every libm (IEEE: sqrt correctly rounded, the others exact) */
static MS double s_sqrt(double x) { return sqrt(x); }
static MS float s_sqrtf(float x) { return sqrtf(x); }
static MS double s_floor(double x) { return floor(x); }
static MS float s_floorf(float x) { return floorf(x); }
static MS double s_ceil(double x) { return ceil(x); }
static MS float s_ceilf(float x) { return ceilf(x); }
static MS double s_trunc(double x) { return trunc(x); }
static MS float s_truncf(float x) { return truncf(x); }
static MS double s_fabs(double x) { return fabs(x); }
static MS float s_fabsf(float x) { return fabsf(x); }
static MS double s_fmod(double a, double b) { return fmod(a, b); }
static MS float s_fmodf(float a, float b) { return fmodf(a, b); }
static MS double s_copysign(double a, double b) { return copysign(a, b); }
static MS float s_copysignf(float a, float b) { return copysignf(a, b); }

static MS void s_abort(void) { wl_fail(WL_TRAP, "abort() called in the DLL"); }
static MS void s_exit(int c) { wl_fail(WL_TRAP, "exit(%d) called in the DLL", c); }
static MS void s_assert(const char *m, const char *f, unsigned l) {
    wl_fail(WL_TRAP, "assertion failed in the DLL: %s (%s:%u)", m ? m : "?", f ? f : "?", l);
}
static MS void s_wassert(const uint16_t *m, const uint16_t *f, unsigned l) {
    char a[200], b[200];
    int i;
    for (i = 0; m && m[i] && i < 199; i++) a[i] = (char)m[i];
    a[i] = 0;
    for (i = 0; f && f[i] && i < 199; i++) b[i] = (char)f[i];
    b[i] = 0;
    wl_fail(WL_TRAP, "assertion failed in the DLL: %s (%s:%u)", a, b, l);
}
static MS void s_amsg_exit(int c) { wl_fail(WL_TRAP, "_amsg_exit(%d) called in the DLL", c); }
static MS void s_invalid_parameter(void) { wl_fail(WL_TRAP, "invalid parameter handler called in the DLL"); }
static MS void s_report_gsfailure(void) { wl_fail(WL_TRAP, "stack buffer overrun detected in the DLL (/GS)"); }
static MS void *s_signal(int s, void *h) { (void)s; (void)h; return NULL; }
static MS void s_noop(void) {}
static MS int s_zero(void) { return 0; }
static MS int s_one(void) { return 1; }

/* stdio: the DLL's FILE pointers. msvcrt's __iob_func returns an array of 48-byte FILE, ucrt's __acrt_iob_func(i) one
   of them: both point into iob_fake; the stdio shims map those three back to stdin/stdout/stderr. */
static uint8_t iob_fake[3 * 48];
static FILE *ms_file(void *f) {
    if ((uint8_t *)f >= iob_fake && (uint8_t *)f < iob_fake + sizeof iob_fake) {
        size_t i = ((uint8_t *)f - iob_fake) / 48;
        return i == 0 ? stdin : (i == 1 ? stdout : stderr);
    }
    return (FILE *)f;
}
static MS void *s_iob_func(void) { return iob_fake; }
static MS void *s_acrt_iob_func(unsigned i) { return iob_fake + 48 * (i < 3 ? i : 2); }

/* printf with a Windows va_list: %[flags][width][.prec][hh|h|l|ll|I64|z|j|t|L]d i u x X o c s p f F e E g G a A % */
static int ms_vformat(char *out, size_t cap, const char *fmt, __builtin_ms_va_list ap) {
    size_t n = 0;
#define PUT(ch) do { if (n + 1 < cap) out[n] = (ch); n++; } while (0)
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { PUT(*p); continue; }
        char spec[64];
        int k = 0;
        spec[k++] = '%';
        p++;
        while (*p && strchr("-+ #0", *p) && k < 40) spec[k++] = *p++;
        if (*p == '*') { k += snprintf(spec + k, sizeof spec - k, "%d", __builtin_va_arg(ap, int)); p++; }
        else while (*p >= '0' && *p <= '9' && k < 50) spec[k++] = *p++;
        if (*p == '.') {
            spec[k++] = *p++;
            if (*p == '*') { k += snprintf(spec + k, sizeof spec - k, "%d", __builtin_va_arg(ap, int)); p++; }
            else while (*p >= '0' && *p <= '9' && k < 58) spec[k++] = *p++;
        }
        int lng = 0;                 /* 0 int, 1 64-bit */
        if (*p == 'h') { p++; if (*p == 'h') p++; }
        else if (*p == 'l') { p++; if (*p == 'l') { p++; lng = 1; } }       /* Windows long is 32-bit */
        else if (*p == 'I' && p[1] == '6' && p[2] == '4') { p += 3; lng = 1; }
        else if (*p == 'I' && p[1] == '3' && p[2] == '2') { p += 3; }
        else if (*p == 'I' || *p == 'z' || *p == 'j' || *p == 't') { p++; lng = 1; }
        else if (*p == 'L') { p++; }
        char c = *p;
        if (!c) break;
        char tmp[512];
        int m = 0;
        if (c == '%') { PUT('%'); continue; }
        if (strchr("diuxXoc", c)) {
            if (lng) { spec[k++] = 'l'; spec[k++] = 'l'; }
            spec[k++] = c;
            spec[k] = 0;
            if (lng) m = snprintf(tmp, sizeof tmp, spec, __builtin_va_arg(ap, long long));
            else m = snprintf(tmp, sizeof tmp, spec, __builtin_va_arg(ap, int));
        } else if (strchr("fFeEgGaA", c)) {
            spec[k++] = c;
            spec[k] = 0;
            m = snprintf(tmp, sizeof tmp, spec, __builtin_va_arg(ap, double));
        } else if (c == 's') {
            spec[k++] = 's';
            spec[k] = 0;
            const char *s = __builtin_va_arg(ap, const char *);
            m = snprintf(tmp, sizeof tmp, spec, s ? s : "(null)");
        } else if (c == 'p') {
            m = snprintf(tmp, sizeof tmp, "%016llX", (unsigned long long)(uintptr_t)__builtin_va_arg(ap, void *));
        } else if (c == 'n') {
            int *ip = __builtin_va_arg(ap, int *);
            if (ip) *ip = (int)n;
            continue;
        } else {
            PUT('%'); PUT(c);
            continue;
        }
        if (m > (int)sizeof tmp - 1) m = sizeof tmp - 1;
        for (int i = 0; i < m; i++) PUT(tmp[i]);
    }
    if (cap) out[n < cap ? n : cap - 1] = 0;
#undef PUT
    return (int)n;
}

static MS int s_vfprintf(void *f, const char *fmt, __builtin_ms_va_list ap) {
    char buf[4096];
    int n = ms_vformat(buf, sizeof buf, fmt, ap);
    fputs(buf, ms_file(f));
    return n;
}
static MS int s_fprintf(void *f, const char *fmt, ...) {
    __builtin_ms_va_list ap;
    __builtin_ms_va_start(ap, fmt);
    int n = s_vfprintf(f, fmt, ap);
    __builtin_ms_va_end(ap);
    return n;
}
static MS int s_printf(const char *fmt, ...) {
    __builtin_ms_va_list ap;
    __builtin_ms_va_start(ap, fmt);
    int n = s_vfprintf(iob_fake + 48, fmt, ap);
    __builtin_ms_va_end(ap);
    return n;
}
static MS int s_vprintf(const char *fmt, __builtin_ms_va_list ap) { return s_vfprintf(iob_fake + 48, fmt, ap); }
static MS int s_vsnprintf(char *b, size_t n, const char *fmt, __builtin_ms_va_list ap) { return ms_vformat(b, n, fmt, ap); }
/* msvcrt's _snprintf / _vsnprintf: -1 when the output was cut (C99's snprintf gives the length it needed) */
static MS int s_ms_vsnprintf(char *b, size_t n, const char *fmt, __builtin_ms_va_list ap) {
    int r = ms_vformat(b, n, fmt, ap);
    return (size_t)r >= n ? -1 : r;
}
static MS int s_ms_snprintf(char *b, size_t n, const char *fmt, ...) {
    __builtin_ms_va_list ap;
    __builtin_ms_va_start(ap, fmt);
    int r = s_ms_vsnprintf(b, n, fmt, ap);
    __builtin_ms_va_end(ap);
    return r;
}
static MS int s_snprintf(char *b, size_t n, const char *fmt, ...) {
    __builtin_ms_va_list ap;
    __builtin_ms_va_start(ap, fmt);
    int r = ms_vformat(b, n, fmt, ap);
    __builtin_ms_va_end(ap);
    return r;
}
static MS int s_sprintf(char *b, const char *fmt, ...) {
    __builtin_ms_va_list ap;
    __builtin_ms_va_start(ap, fmt);
    int r = ms_vformat(b, (size_t)1 << 30, fmt, ap);
    __builtin_ms_va_end(ap);
    return r;
}
/* ucrt: __stdio_common_vfprintf(options, FILE*, format, locale, va_list), __stdio_common_vsprintf(options, buf, n, ...) */
static MS int s_common_vfprintf(uint64_t o, void *f, const char *fmt, void *loc, __builtin_ms_va_list ap) {
    (void)o; (void)loc;
    return s_vfprintf(f, fmt, ap);
}
static MS int s_common_vsprintf(uint64_t o, char *b, size_t n, const char *fmt, void *loc, __builtin_ms_va_list ap) {
    (void)o; (void)loc;
    int r = ms_vformat(b, n, fmt, ap);
    return (b && n && (size_t)r >= n) ? -1 : r;
}
static MS int s_puts(const char *s) { return puts(s); }
static MS int s_putchar(int c) { return putchar(c); }
static MS int s_fputs(const char *s, void *f) { return fputs(s, ms_file(f)); }
static MS int s_fputc(int c, void *f) { return fputc(c, ms_file(f)); }
static MS size_t s_fwrite(const void *p, size_t a, size_t b, void *f) { return fwrite(p, a, b, ms_file(f)); }
static MS size_t s_fread(void *p, size_t a, size_t b, void *f) { return fread(p, a, b, ms_file(f)); }
static MS int s_fflush(void *f) { return fflush(f ? ms_file(f) : NULL); }
static MS void *s_fopen(const char *p, const char *m) {
    char mm[16];
    int k = 0;
    for (; *m && k < 15; m++) if (*m != 't') mm[k++] = *m;     /* 't' (text) is Windows only */
    mm[k] = 0;
    return fopen(p, mm);
}
static MS int s_fclose(void *f) { return fclose(ms_file(f)); }
static MS int32_t s_ftell(void *f) { return (int32_t)ftell(ms_file(f)); }
static MS long long s_ftelli64(void *f) { return ftello(ms_file(f)); }
static MS int s_fseek(void *f, int32_t o, int w) { return fseek(ms_file(f), o, w); }
static MS int s_fseeki64(void *f, long long o, int w) { return fseeko(ms_file(f), o, w); }
static MS char *s_strerror(int e) { return strerror(e); }

static struct { char *dp, *ts, *gr; } s_lconv_v = {".", "", ""};
static MS void *s_localeconv(void) { return &s_lconv_v; }

/* setjmp / longjmp with the Windows convention: rbx rsp rbp rsi rdi r12-r15 rip, mxcsr + x87 cw, xmm6-15 (256 bytes,
   the size of the Windows _JUMP_BUFFER). No unwinding: C code only. */
__asm__(
    ".text\n"
    ".p2align 4\n"
    ".type s_setjmp,@function\n"
    "s_setjmp:\n"                   /* rcx = buf (rdx = frame, ignored) */
    "  mov %rbx, 0(%rcx)\n"
    "  lea 8(%rsp), %rax\n"
    "  mov %rax, 8(%rcx)\n"
    "  mov %rbp, 16(%rcx)\n"
    "  mov %rsi, 24(%rcx)\n"
    "  mov %rdi, 32(%rcx)\n"
    "  mov %r12, 40(%rcx)\n"
    "  mov %r13, 48(%rcx)\n"
    "  mov %r14, 56(%rcx)\n"
    "  mov %r15, 64(%rcx)\n"
    "  mov (%rsp), %rax\n"
    "  mov %rax, 72(%rcx)\n"
    "  stmxcsr 80(%rcx)\n"
    "  fnstcw 84(%rcx)\n"
    "  movdqu %xmm6, 96(%rcx)\n"
    "  movdqu %xmm7, 112(%rcx)\n"
    "  movdqu %xmm8, 128(%rcx)\n"
    "  movdqu %xmm9, 144(%rcx)\n"
    "  movdqu %xmm10, 160(%rcx)\n"
    "  movdqu %xmm11, 176(%rcx)\n"
    "  movdqu %xmm12, 192(%rcx)\n"
    "  movdqu %xmm13, 208(%rcx)\n"
    "  movdqu %xmm14, 224(%rcx)\n"
    "  movdqu %xmm15, 240(%rcx)\n"
    "  xor %eax, %eax\n"
    "  ret\n"
    ".size s_setjmp, .-s_setjmp\n"
    ".p2align 4\n"
    ".type s_longjmp,@function\n"
    "s_longjmp:\n"                  /* rcx = buf, edx = value */
    "  mov %edx, %eax\n"
    "  test %eax, %eax\n"
    "  jnz 1f\n"
    "  inc %eax\n"
    "1:\n"
    "  mov 0(%rcx), %rbx\n"
    "  mov 16(%rcx), %rbp\n"
    "  mov 24(%rcx), %rsi\n"
    "  mov 32(%rcx), %rdi\n"
    "  mov 40(%rcx), %r12\n"
    "  mov 48(%rcx), %r13\n"
    "  mov 56(%rcx), %r14\n"
    "  mov 64(%rcx), %r15\n"
    "  ldmxcsr 80(%rcx)\n"
    "  fldcw 84(%rcx)\n"
    "  movdqu 96(%rcx), %xmm6\n"
    "  movdqu 112(%rcx), %xmm7\n"
    "  movdqu 128(%rcx), %xmm8\n"
    "  movdqu 144(%rcx), %xmm9\n"
    "  movdqu 160(%rcx), %xmm10\n"
    "  movdqu 176(%rcx), %xmm11\n"
    "  movdqu 192(%rcx), %xmm12\n"
    "  movdqu 208(%rcx), %xmm13\n"
    "  movdqu 224(%rcx), %xmm14\n"
    "  movdqu 240(%rcx), %xmm15\n"
    "  mov 8(%rcx), %rsp\n"
    "  jmp *72(%rcx)\n"
    ".size s_longjmp, .-s_longjmp\n");
MS int s_setjmp(void *buf, void *frame);
MS void s_longjmp(void *buf, int v);

/* ------------------------------------------------------------------------------------------------------------------ */
/* shims: kernel32                                                                                                    */

static MS uint32_t k_GetLastError(void) { return *tls_last_error(); }
static MS void k_SetLastError(uint32_t e) { *tls_last_error() = e; }
static MS uint32_t k_GetCurrentThreadId(void) { return (uint32_t)syscall(SYS_gettid); }
static MS uint32_t k_GetCurrentProcessId(void) { return (uint32_t)getpid(); }
static MS intptr_t k_GetCurrentProcess(void) { return -1; }
static MS intptr_t k_GetCurrentThread(void) { return -2; }
static MS int k_QueryPerformanceCounter(int64_t *c) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    *c = (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
    return 1;
}
static MS int k_QueryPerformanceFrequency(int64_t *f) { *f = 1000000000LL; return 1; }
static MS void k_GetSystemTimeAsFileTime(uint64_t *ft) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    *ft = ((uint64_t)ts.tv_sec + 11644473600ULL) * 10000000ULL + (uint64_t)ts.tv_nsec / 100;
}
static MS void k_GetSystemTimePreciseAsFileTime(uint64_t *ft) { k_GetSystemTimeAsFileTime(ft); }
static MS uint64_t k_GetTickCount64(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static MS uint32_t k_GetTickCount(void) { return (uint32_t)k_GetTickCount64(); }
static MS void k_Sleep(uint32_t ms) { usleep((useconds_t)ms * 1000); }
static MS int k_IsDebuggerPresent(void) { return 0; }
static MS int k_IsProcessorFeaturePresent(uint32_t f) {
    switch (f) {
    case 6: case 10: case 12: case 13: case 23: return 1;   /* XMMI, XMMI64, NX, SSE3, FASTFAIL */
    default: return 0;
    }
}
static MS void k_OutputDebugStringA(const char *s) { if (s) fprintf(stderr, "[dll] %s", s); }
static MS int k_DisableThreadLibraryCalls(void *h) { (void)h; return 1; }
static MS void *k_EncodePointer(void *p) { return p; }
static MS void k_InitializeSListHead(void *h) { memset(h, 0, 16); }

/* CRITICAL_SECTION is 40 bytes on x64, a glibc pthread_mutex_t too: the recursive mutex lives in place */
_Static_assert(sizeof(pthread_mutex_t) <= 40, "CRITICAL_SECTION");
static MS void k_InitializeCriticalSection(void *cs) {
    pthread_mutexattr_t a;
    pthread_mutexattr_init(&a);
    pthread_mutexattr_settype(&a, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init((pthread_mutex_t *)cs, &a);
    pthread_mutexattr_destroy(&a);
}
static MS int k_InitializeCriticalSectionAndSpinCount(void *cs, uint32_t n) { (void)n; k_InitializeCriticalSection(cs); return 1; }
static MS int k_InitializeCriticalSectionEx(void *cs, uint32_t n, uint32_t fl) { (void)n; (void)fl; k_InitializeCriticalSection(cs); return 1; }
static MS void k_EnterCriticalSection(void *cs) { pthread_mutex_lock((pthread_mutex_t *)cs); }
static MS void k_LeaveCriticalSection(void *cs) { pthread_mutex_unlock((pthread_mutex_t *)cs); }
static MS int k_TryEnterCriticalSection(void *cs) { return pthread_mutex_trylock((pthread_mutex_t *)cs) == 0; }
static MS void k_DeleteCriticalSection(void *cs) { pthread_mutex_destroy((pthread_mutex_t *)cs); }

/* TLS / FLS slots */
#define WL_TLS 1088
static __thread void *t_tls[WL_TLS];
static WL_TLS_ACCESSOR void **tls_slots(void) { return t_tls; }
static uint8_t g_tls_used[WL_TLS];
static pthread_mutex_t g_tls_lock = PTHREAD_MUTEX_INITIALIZER;
static MS uint32_t k_TlsAlloc(void) {
    uint32_t r = 0xFFFFFFFF;
    pthread_mutex_lock(&g_tls_lock);
    for (uint32_t i = 0; i < WL_TLS; i++)
        if (!g_tls_used[i]) { g_tls_used[i] = 1; r = i; break; }
    pthread_mutex_unlock(&g_tls_lock);
    return r;
}
static MS uint32_t k_FlsAlloc(void *cb) { (void)cb; return k_TlsAlloc(); }
static MS int k_TlsFree(uint32_t i) { if (i < WL_TLS) g_tls_used[i] = 0; return 1; }
static MS void *k_TlsGetValue(uint32_t i) { *tls_last_error() = 0; return i < WL_TLS ? tls_slots()[i] : NULL; }
static MS int k_TlsSetValue(uint32_t i, void *v) { if (i >= WL_TLS) return 0; tls_slots()[i] = v; return 1; }

/* heaps: one heap, the process allocator */
static MS void *k_GetProcessHeap(void) { return (void *)0x484541500000ULL; }
static MS void *k_HeapAlloc(void *h, uint32_t fl, size_t n) { (void)h; return (fl & 8) ? calloc(1, n ? n : 1) : malloc(n ? n : 1); }
static MS void *k_HeapReAlloc(void *h, uint32_t fl, void *p, size_t n) {
    (void)h;
    size_t old = p ? malloc_usable_size(p) : 0;
    void *q = realloc(p, n ? n : 1);
    if (q && (fl & 8) && n > old) memset((uint8_t *)q + old, 0, n - old);       /* HEAP_ZERO_MEMORY: the grown part */
    return q;
}
static MS int k_HeapFree(void *h, uint32_t fl, void *p) { (void)h; (void)fl; free(p); return 1; }
static MS size_t k_HeapSize(void *h, uint32_t fl, void *p) { (void)h; (void)fl; return malloc_usable_size(p); }

/* virtual memory */
static int prot_from_win(uint32_t p) {
    switch (p & 0xFF) {
    case 0x01: return PROT_NONE;
    case 0x02: return PROT_READ;
    case 0x04: case 0x08: return PROT_READ | PROT_WRITE;
    case 0x10: return PROT_EXEC;
    case 0x20: return PROT_READ | PROT_EXEC;
    case 0x40: case 0x80: return PROT_READ | PROT_WRITE | PROT_EXEC;
    default: return PROT_READ | PROT_WRITE;
    }
}
/* the reservations VirtualAlloc made (VirtualFree MEM_RELEASE gets size 0: the whole reservation) */
#define WL_RES 4096
static struct { uintptr_t a; size_t n; } g_res[WL_RES];
static pthread_mutex_t g_res_lock = PTHREAD_MUTEX_INITIALIZER;
static void res_add(void *a, size_t n) {
    pthread_mutex_lock(&g_res_lock);
    for (int i = 0; i < WL_RES; i++)
        if (!g_res[i].a) { g_res[i].a = (uintptr_t)a; g_res[i].n = n; break; }
    pthread_mutex_unlock(&g_res_lock);
}
static size_t res_take(void *a) {
    size_t n = 0;
    pthread_mutex_lock(&g_res_lock);
    for (int i = 0; i < WL_RES; i++)
        if (g_res[i].a == (uintptr_t)a) { n = g_res[i].n; g_res[i].a = 0; break; }
    pthread_mutex_unlock(&g_res_lock);
    return n;
}
static MS void *k_VirtualAlloc(void *addr, size_t n, uint32_t type, uint32_t prot) {
    if (!(type & 0x2000) && !(type & 0x1000)) return NULL;
    if (addr && !(type & 0x2000)) {                 /* commit inside a reservation */
        uintptr_t a = (uintptr_t)addr & ~(uintptr_t)0xFFF;
        size_t len = (((uintptr_t)addr + n + 0xFFF) & ~(uintptr_t)0xFFF) - a;
        return mprotect((void *)a, len, prot_from_win(prot)) == 0 ? addr : NULL;
    }
    int fl = MAP_PRIVATE | MAP_ANONYMOUS | (addr ? MAP_FIXED_NOREPLACE : 0);
    void *p = mmap(addr, n, (type & 0x1000) ? prot_from_win(prot) : PROT_NONE, fl, -1, 0);
    if (p == MAP_FAILED) { *tls_last_error() = 8; return NULL; }
    res_add(p, n);
    return p;
}
static MS int k_VirtualFree(void *a, size_t n, uint32_t type) {
    if (type & 0x8000) {                            /* MEM_RELEASE (size 0): the whole reservation */
        size_t r = res_take(a);
        if (!r) { *tls_last_error() = 487; return 0; }
        return munmap(a, r) == 0;
    }
    /* MEM_DECOMMIT: the pages' contents dropped and their access refused, as on Windows */
    uintptr_t s0 = (uintptr_t)a & ~(uintptr_t)0xFFF;
    size_t len = (((uintptr_t)a + n + 0xFFF) & ~(uintptr_t)0xFFF) - s0;
    return madvise((void *)s0, len, MADV_DONTNEED) == 0 && mprotect((void *)s0, len, PROT_NONE) == 0;
}
/* the Windows protection of the page holding a (from /proc/self/maps), 0 when unmapped */
static uint32_t win_prot_of(uintptr_t a) {
    FILE *f = fopen("/proc/self/maps", "re");
    if (!f) return 0x40;
    char line[512];
    uint32_t r = 0;
    while (fgets(line, sizeof line, f)) {
        unsigned long lo, hi;
        char pr[8];
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, pr) != 3) continue;
        if (a < lo || a >= hi) continue;
        int R = pr[0] == 'r', W = pr[1] == 'w', X = pr[2] == 'x';
        r = X ? (W ? 0x40 : (R ? 0x20 : 0x10)) : (W ? 0x04 : (R ? 0x02 : 0x01));
        break;
    }
    fclose(f);
    return r;
}
static MS int k_VirtualProtect(void *a, size_t n, uint32_t prot, uint32_t *old) {
    uintptr_t s = (uintptr_t)a & ~(uintptr_t)0xFFF;
    size_t len = (((uintptr_t)a + n + 0xFFF) & ~(uintptr_t)0xFFF) - s;
    uint32_t was = win_prot_of(s);
    if (!was) { *tls_last_error() = 487; return 0; }           /* ERROR_INVALID_ADDRESS */
    if (old) *old = was;
    return mprotect((void *)s, len, prot_from_win(prot)) == 0;
}
typedef struct { void *base, *alloc_base; uint32_t alloc_prot; uint16_t part; size_t size; uint32_t state, prot, type; } MBI;
static MS size_t k_VirtualQuery(const void *a, MBI *mbi, size_t n) {
    if (n < sizeof(MBI)) return 0;
    memset(mbi, 0, sizeof *mbi);
    uintptr_t p = (uintptr_t)a & ~(uintptr_t)0xFFF;
    uint32_t prot = win_prot_of(p);
    wl_module *m = wl_find_module_by_addr(p);
    mbi->base = (void *)p;
    mbi->size = 4096;
    if (!prot) {                                    /* not mapped: a free region */
        mbi->state = 0x10000;
        mbi->prot = 0x01;
        return sizeof(MBI);
    }
    mbi->alloc_base = m ? (void *)m->base : (void *)p;
    mbi->alloc_prot = prot;
    if (m) mbi->size = (size_t)(m->base + m->size - (uint8_t *)p);
    mbi->state = 0x1000;                            /* MEM_COMMIT */
    mbi->prot = prot;
    mbi->type = m ? 0x1000000 : 0x20000;            /* MEM_IMAGE / MEM_PRIVATE */
    return sizeof(MBI);
}
static MS int k_FlushInstructionCache(void *p, void *a, size_t n) { (void)p; (void)a; (void)n; return 1; }

/* modules */
static MS void *k_GetModuleHandleA(const char *name);
static MS void *k_GetProcAddress(void *h, const char *name);
static MS void *k_GetModuleHandleW(const uint16_t *name) {
    if (!name) return k_GetModuleHandleA(NULL);
    char a[260];
    int i;
    for (i = 0; name[i] && i < 259; i++) a[i] = (char)name[i];
    a[i] = 0;
    return k_GetModuleHandleA(a);
}
static MS int k_GetModuleHandleExA(uint32_t fl, const char *name, void **out) {
    if (fl & 4) {                                   /* FROM_ADDRESS */
        wl_module *m = wl_find_module_by_addr((uintptr_t)name);
        *out = m ? m->base : NULL;
        return m != NULL;
    }
    *out = k_GetModuleHandleA(name);
    return *out != NULL;
}
static MS int k_GetModuleHandleExW(uint32_t fl, const uint16_t *name, void **out) {
    if (fl & 4) return k_GetModuleHandleExA(fl, (const char *)name, out);
    *out = k_GetModuleHandleW(name);
    return *out != NULL;
}
static MS void k_RtlCaptureContext(void *ctx) { memset(ctx, 0, 1232); }
static MS void *k_RtlLookupFunctionEntry(uint64_t pc, uint64_t *base, void *h) { (void)pc; (void)h; if (base) *base = 0; return NULL; }
static MS void *k_SetUnhandledExceptionFilter(void *f) { (void)f; return NULL; }
static MS int32_t k_UnhandledExceptionFilter(void *e) { (void)e; return 1; }
static MS int k_TerminateProcess(void *h, uint32_t c) {
    (void)h;
    wl_fail(WL_TRAP, "TerminateProcess(%u) called in the DLL (a failed security check or a fatal CRT error)", c);
}
static MS void k_ExitProcess(uint32_t c) { wl_fail(WL_TRAP, "ExitProcess(%u) called in the DLL", c); }
static MS void k_RaiseException(uint32_t code, uint32_t fl, uint32_t n, const uintptr_t *args) {
    (void)fl; (void)n; (void)args;
    wl_fail(WL_TRAP, "RaiseException(0x%08X) in the DLL", code);
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* the shim table                                                                                                     */

typedef struct { const char *name; void *fn; } shim;

#define S(n, f) {n, (void *)(f)}
static const shim crt_shims[] = {
    S("memcpy", s_memcpy), S("memmove", s_memmove), S("memset", s_memset), S("memcmp", s_memcmp), S("memchr", s_memchr),
    S("strlen", s_strlen), S("strnlen", s_strnlen), S("strcmp", s_strcmp), S("strncmp", s_strncmp),
    S("_stricmp", s_stricmp), S("_strnicmp", s_strnicmp), S("strcpy", s_strcpy), S("strncpy", s_strncpy),
    S("strcat", s_strcat), S("strncat", s_strncat), S("strchr", s_strchr), S("strrchr", s_strrchr),
    S("strstr", s_strstr), S("_strdup", s_strdup), S("strdup", s_strdup), S("wcslen", s_wcslen),
    S("malloc", s_malloc), S("calloc", s_calloc), S("realloc", s_realloc), S("free", s_free), S("_msize", s_msize),
    S("_aligned_malloc", s_aligned_malloc), S("_aligned_free", s_aligned_free),
    S("_errno", s_errno), S("abs", s_abs), S("labs", s_abs), S("llabs", s_llabs), S("_abs64", s_llabs),
    S("atoi", s_atoi), S("atol", s_atol), S("strtol", s_strtol), S("strtoul", s_strtoul), S("strtoll", s_strtoll),
    S("_strtoi64", s_strtoll), S("strtoull", s_strtoull), S("_strtoui64", s_strtoull), S("strtod", s_strtod),
    S("atof", s_atof), S("getenv", s_getenv), S("_ultoa", s_ultoa),
    S("sqrt", s_sqrt), S("sqrtf", s_sqrtf), S("floor", s_floor), S("floorf", s_floorf), S("ceil", s_ceil),
    S("ceilf", s_ceilf), S("trunc", s_trunc), S("truncf", s_truncf), S("fabs", s_fabs), S("fabsf", s_fabsf),
    S("fmod", s_fmod), S("fmodf", s_fmodf), S("copysign", s_copysign), S("_copysign", s_copysign),
    S("copysignf", s_copysignf),
    S("abort", s_abort), S("exit", s_exit), S("_exit", s_exit), S("_Exit", s_exit), S("_assert", s_assert),
    S("_wassert", s_wassert), S("_amsg_exit", s_amsg_exit), S("_invalid_parameter_noinfo", s_invalid_parameter),
    S("_invalid_parameter_noinfo_noreturn", s_invalid_parameter), S("__report_gsfailure", s_report_gsfailure),
    S("signal", s_signal), S("_lock", s_noop), S("_unlock", s_noop), S("__setusermatherr", s_noop),
    S("__std_type_info_destroy_list", s_noop), S("___lc_codepage_func", s_zero), S("___mb_cur_max_func", s_one),
    S("localeconv", s_localeconv), S("strerror", s_strerror),
    S("__iob_func", s_iob_func), S("__acrt_iob_func", s_acrt_iob_func), S("printf", s_printf), S("fprintf", s_fprintf),
    S("vfprintf", s_vfprintf), S("vprintf", s_vprintf), S("vsnprintf", s_vsnprintf), S("_vsnprintf", s_ms_vsnprintf),
    S("snprintf", s_snprintf), S("_snprintf", s_ms_snprintf), S("sprintf", s_sprintf),
    S("__stdio_common_vfprintf", s_common_vfprintf), S("__stdio_common_vsprintf", s_common_vsprintf),
    S("puts", s_puts), S("putchar", s_putchar), S("fputs", s_fputs), S("fputc", s_fputc), S("putc", s_fputc),
    S("fwrite", s_fwrite), S("fread", s_fread), S("fflush", s_fflush), S("fopen", s_fopen), S("fclose", s_fclose),
    S("ftell", s_ftell), S("_ftelli64", s_ftelli64), S("fseek", s_fseek), S("_fseeki64", s_fseeki64),
    S("_setjmp", s_setjmp), S("setjmp", s_setjmp), S("_setjmpex", s_setjmp), S("__intrinsic_setjmp", s_setjmp),
    S("__intrinsic_setjmpex", s_setjmp), S("longjmp", s_longjmp),
};
static const shim k32_shims[] = {
    S("GetLastError", k_GetLastError), S("SetLastError", k_SetLastError),
    S("GetCurrentThreadId", k_GetCurrentThreadId), S("GetCurrentProcessId", k_GetCurrentProcessId),
    S("GetCurrentProcess", k_GetCurrentProcess), S("GetCurrentThread", k_GetCurrentThread),
    S("QueryPerformanceCounter", k_QueryPerformanceCounter), S("QueryPerformanceFrequency", k_QueryPerformanceFrequency),
    S("GetSystemTimeAsFileTime", k_GetSystemTimeAsFileTime),
    S("GetSystemTimePreciseAsFileTime", k_GetSystemTimePreciseAsFileTime),
    S("GetTickCount", k_GetTickCount), S("GetTickCount64", k_GetTickCount64), S("Sleep", k_Sleep),
    S("IsDebuggerPresent", k_IsDebuggerPresent), S("IsProcessorFeaturePresent", k_IsProcessorFeaturePresent),
    S("OutputDebugStringA", k_OutputDebugStringA), S("DisableThreadLibraryCalls", k_DisableThreadLibraryCalls),
    S("EncodePointer", k_EncodePointer), S("DecodePointer", k_EncodePointer), S("InitializeSListHead", k_InitializeSListHead),
    S("InitializeCriticalSection", k_InitializeCriticalSection),
    S("InitializeCriticalSectionAndSpinCount", k_InitializeCriticalSectionAndSpinCount),
    S("InitializeCriticalSectionEx", k_InitializeCriticalSectionEx), S("EnterCriticalSection", k_EnterCriticalSection),
    S("LeaveCriticalSection", k_LeaveCriticalSection), S("TryEnterCriticalSection", k_TryEnterCriticalSection),
    S("DeleteCriticalSection", k_DeleteCriticalSection),
    S("TlsAlloc", k_TlsAlloc), S("TlsFree", k_TlsFree), S("TlsGetValue", k_TlsGetValue), S("TlsSetValue", k_TlsSetValue),
    S("FlsAlloc", k_FlsAlloc), S("FlsFree", k_TlsFree), S("FlsGetValue", k_TlsGetValue), S("FlsSetValue", k_TlsSetValue),
    S("GetProcessHeap", k_GetProcessHeap), S("HeapAlloc", k_HeapAlloc), S("HeapReAlloc", k_HeapReAlloc),
    S("HeapFree", k_HeapFree), S("HeapSize", k_HeapSize),
    S("VirtualAlloc", k_VirtualAlloc), S("VirtualFree", k_VirtualFree), S("VirtualProtect", k_VirtualProtect),
    S("VirtualQuery", k_VirtualQuery), S("FlushInstructionCache", k_FlushInstructionCache),
    S("GetModuleHandleA", k_GetModuleHandleA), S("GetModuleHandleW", k_GetModuleHandleW),
    S("GetModuleHandleExA", k_GetModuleHandleExA), S("GetModuleHandleExW", k_GetModuleHandleExW),
    S("GetProcAddress", k_GetProcAddress),
    S("RtlCaptureContext", k_RtlCaptureContext), S("RtlLookupFunctionEntry", k_RtlLookupFunctionEntry),
    S("SetUnhandledExceptionFilter", k_SetUnhandledExceptionFilter),
    S("UnhandledExceptionFilter", k_UnhandledExceptionFilter), S("TerminateProcess", k_TerminateProcess),
    S("ExitProcess", k_ExitProcess), S("RaiseException", k_RaiseException),
};
#undef S

static int is_crt_dll(const char *dll) {
    return !strcasecmp(dll, "msvcrt.dll") || !strcasecmp(dll, "ucrtbase.dll") || !strncasecmp(dll, "api-ms-win-crt-", 15) ||
           !strncasecmp(dll, "vcruntime", 9) || !strncasecmp(dll, "msvcr", 5);
}
static int is_k32_dll(const char *dll) {
    return !strcasecmp(dll, "kernel32.dll") || !strcasecmp(dll, "kernelbase.dll") || !strcasecmp(dll, "ntdll.dll") ||
           !strncasecmp(dll, "api-ms-win-core-", 16);
}

static void *shim_lookup(const char *dll, const char *name) {
    const shim *t = NULL;
    size_t n = 0;
    if (!dll || is_crt_dll(dll)) {
        for (size_t i = 0; i < sizeof crt_shims / sizeof *crt_shims; i++)
            if (!strcmp(crt_shims[i].name, name)) return crt_shims[i].fn;
    }
    if (!dll || is_k32_dll(dll)) { t = k32_shims; n = sizeof k32_shims / sizeof *k32_shims; }
    for (size_t i = 0; i < n; i++)
        if (!strcmp(t[i].name, name)) return t[i].fn;
    return NULL;
}

#define FAKE_K32 ((void *)0x4B3332000000ULL)
#define FAKE_CRT ((void *)0x435254000000ULL)
#define FAKE_EXE ((void *)0x455845000000ULL)

static MS void *k_GetModuleHandleA(const char *name) {
    if (!name) return FAKE_EXE;
    char nm[260];                                   /* (a copy: a bad pointer faults here, never under the lock) */
    snprintf(nm, sizeof nm, "%s", name);
    const char *b = strrchr(nm, '\\');
    b = b ? b + 1 : nm;
    if (is_k32_dll(b) || !strcasecmp(b, "kernel32")) return FAKE_K32;
    if (is_crt_dll(b) || !strcasecmp(b, "msvcrt")) return FAKE_CRT;
    void *r = NULL;
    pthread_mutex_lock(&g_mods_lock);
    for (wl_module *m = g_mods; m; m = m->next)
        if (!strcasecmp(m->name, b)) { r = m->base; break; }
    pthread_mutex_unlock(&g_mods_lock);
    if (!r) *tls_last_error() = 126;
    return r;
}
static MS void *k_GetProcAddress(void *h, const char *name) {
    if ((uintptr_t)name < 0x10000) return NULL;     /* by ordinal: not supported */
    if (h == FAKE_K32) return shim_lookup("kernel32.dll", name);
    if (h == FAKE_CRT) return shim_lookup("msvcrt.dll", name);
    wl_module *m = wl_find_module_by_addr((uintptr_t)h);
    void *r = m ? wl_module_sym(m, name) : NULL;
    if (!r) *tls_last_error() = 127;
    return r;
}

/* ------------------------------------------------------------------------------------------------------------------ */
/* the loader                                                                                                         */

static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint64_t rd64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }

static int set_err(char *err, size_t n, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static int set_err(char *err, size_t n, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (err && n) vsnprintf(err, n, fmt, ap);
    va_end(ap);
    return 0;
}

/* a trap stub: mov ecx, idx ; movabs rax, wl_unimplemented ; jmp rax  (17 bytes, padded to 32) */
static void emit_trap(uint8_t *p, uint32_t idx) {
    p[0] = 0xB9;
    memcpy(p + 1, &idx, 4);
    p[5] = 0x48;
    p[6] = 0xB8;
    uint64_t f = (uint64_t)(uintptr_t)wl_unimplemented;
    memcpy(p + 7, &f, 8);
    p[15] = 0xFF;
    p[16] = 0xE0;
    memset(p + 17, 0xCC, 15);
}

/* Loads a PE32+ x86-64 DLL. Returns its base (the HMODULE) or NULL with the reason in err. Every call maps a new,
   independent copy of the image (unlike LoadLibrary, which returns the same module for the same path). base: where to
   map it (0: the image's preferred base when free, else anywhere). */
EXPORT void *wl_load(const char *path, uint64_t want, char *err, size_t errn) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) { set_err(err, errn, "cannot open %s: %s", path, strerror(errno)); return NULL; }
    struct stat st;
    fstat(fd, &st);
    size_t fsz = (size_t)st.st_size;
    uint8_t *f = mmap(NULL, fsz, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (f == MAP_FAILED) { set_err(err, errn, "cannot map %s", path); return NULL; }
    uint8_t *img = NULL;
    wl_module *mod = NULL;
    if (fsz < 0x200 || f[0] != 'M' || f[1] != 'Z') { set_err(err, errn, "%s is not a PE file", path); goto fail; }
    uint32_t pe = rd32(f + 0x3C);
    if ((size_t)pe + 24 > fsz || memcmp(f + pe, "PE\0\0", 4)) { set_err(err, errn, "%s: no PE header", path); goto fail; }
    uint16_t machine = rd16(f + pe + 4), nsec = rd16(f + pe + 6), optsz = rd16(f + pe + 20);
    if ((size_t)pe + 24 + optsz + 40 * (size_t)nsec > fsz || optsz < 112) {
        set_err(err, errn, "%s: truncated PE headers", path);
        goto fail;
    }
    const uint8_t *opt = f + pe + 24;
    if (machine != 0x8664 || rd16(opt) != 0x20B) { set_err(err, errn, "%s is not a 64-bit x86 PE", path); goto fail; }
    uint64_t pref = rd64(opt + 24);
    uint32_t salign = rd32(opt + 32), imgsz = rd32(opt + 56), hdrsz = rd32(opt + 60);
    uint32_t ndirs = rd32(opt + 108);
    if (ndirs > (optsz - 112u) / 8) ndirs = (optsz - 112u) / 8;      /* the directories the optional header holds */
    const uint8_t *dirs = opt + 112;
    const uint8_t *sec = opt + optsz;
    size_t size = ((size_t)imgsz + 0xFFF) & ~(size_t)0xFFF;
    if (!imgsz || hdrsz > imgsz) { set_err(err, errn, "%s: bad image size", path); goto fail; }
#define IN_IMG(rva, len) ((uint64_t)(rva) + (uint64_t)(len) <= (uint64_t)size)

    uint64_t at = want ? want : pref;
    img = mmap((void *)(uintptr_t)at, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (img == MAP_FAILED || img != (uint8_t *)(uintptr_t)at) {
        if (img != MAP_FAILED) munmap(img, size);
        if (want) { img = NULL; set_err(err, errn, "%s: cannot map at 0x%llx", path, (unsigned long long)want); goto fail; }
        img = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    }
    if (img == MAP_FAILED) { img = NULL; set_err(err, errn, "%s: no memory for the image (%zu bytes)", path, size); goto fail; }
    memcpy(img, f, hdrsz < fsz ? hdrsz : fsz);
    for (unsigned i = 0; i < nsec; i++) {
        const uint8_t *s = sec + 40 * i;
        uint32_t vsz = rd32(s + 8), va = rd32(s + 12), rsz = rd32(s + 16), rptr = rd32(s + 20);
        uint32_t n = rsz < vsz || vsz == 0 ? rsz : vsz;       /* the bytes the file gives */
        if (!IN_IMG(va, vsz) || !IN_IMG(va, n) || (n && (uint64_t)rptr + n > fsz)) {
            set_err(err, errn, "%s: section %u outside the image or the file", path, i);
            goto fail;
        }
        if (n) memcpy(img + va, f + rptr, n);
    }

    /* base relocations */
    int64_t delta = (int64_t)((uintptr_t)img - pref);
    if (delta) {
        uint32_t rva = ndirs > 5 ? rd32(dirs + 8 * 5) : 0, rsz = ndirs > 5 ? rd32(dirs + 8 * 5 + 4) : 0;
        if (!rva || !IN_IMG(rva, rsz)) {
            set_err(err, errn, "%s: cannot load at 0x%llx and has no usable relocations", path, (unsigned long long)pref);
            goto fail;
        }
        uint8_t *p = img + rva, *end = img + rva + rsz;
        while (p + 8 <= end) {
            uint32_t page = rd32(p), bsz = rd32(p + 4);
            if (bsz < 8 || p + bsz > end) break;
            for (uint32_t k = 8; k + 2 <= bsz; k += 2) {
                uint16_t e = rd16(p + k);
                if ((e >> 12) && !IN_IMG((uint64_t)page + (e & 0xFFF), 8)) {
                    set_err(err, errn, "%s: relocation outside the image", path);
                    goto fail;
                }
                uint8_t *t = img + page + (e & 0xFFF);
                switch (e >> 12) {
                case 0: break;
                case 10: { uint64_t v = rd64(t) + (uint64_t)delta; memcpy(t, &v, 8); break; }
                case 3: { uint32_t v = rd32(t) + (uint32_t)delta; memcpy(t, &v, 4); break; }
                default: set_err(err, errn, "%s: relocation type %u not supported", path, e >> 12); goto fail;
                }
            }
            p += bsz;
        }
    }

    mod = calloc(1, sizeof *mod);
    mod->base = img;
    mod->size = size;
    snprintf(mod->path, sizeof mod->path, "%s", path);
    const char *bn = strrchr(path, '/');
    snprintf(mod->name, sizeof mod->name, "%s", bn ? bn + 1 : path);

    /* exports */
    if (ndirs > 0 && rd32(dirs) && IN_IMG(rd32(dirs), 40)) {
        uint8_t *ed = img + rd32(dirs);
        uint32_t nfn = rd32(ed + 20), nnames = rd32(ed + 24), afn = rd32(ed + 28), anames = rd32(ed + 32),
                 aords = rd32(ed + 36);
        if (!IN_IMG(afn, 4 * (uint64_t)nfn) || !IN_IMG(anames, 4 * (uint64_t)nnames) || !IN_IMG(aords, 2 * (uint64_t)nnames)) {
            set_err(err, errn, "%s: export table outside the image", path);
            goto fail;
        }
        mod->exports = calloc(nnames ? nnames : 1, sizeof *mod->exports);
        for (uint32_t i = 0; i < nnames; i++) {
            uint32_t nrva = rd32(img + anames + 4 * i);
            uint16_t o = rd16(img + aords + 2 * i);
            if (o >= nfn || !IN_IMG(nrva, 1) || !memchr(img + nrva, 0, size - nrva)) continue;
            const char *nm = (const char *)img + nrva;
            mod->exports[mod->nexports].name = nm;
            mod->exports[mod->nexports].rva = rd32(img + afn + 4 * o);
            mod->nexports++;
        }
        qsort(mod->exports, mod->nexports, sizeof *mod->exports, cmp_export);
    }

    /* imports: shims, or a named trap */
    if (ndirs > 1 && rd32(dirs + 8)) {
        size_t nimp = 0;
        for (uint8_t *d = img + rd32(dirs + 8);; d += 20) {
            if (!IN_IMG(d - img, 20)) { set_err(err, errn, "%s: import table outside the image", path); goto fail; }
            if (!rd32(d + 12)) break;
            uint32_t names = rd32(d) ? rd32(d) : rd32(d + 16);
            if (!IN_IMG(rd32(d + 12), 1) || !memchr(img + rd32(d + 12), 0, size - rd32(d + 12))) {
                set_err(err, errn, "%s: an import's DLL name is outside the image", path);
                goto fail;
            }
            for (uint8_t *t = img + names;; t += 8) {
                if (!IN_IMG(t - img, 8) || !IN_IMG(rd32(d + 16) + (t - img - names), 8)) {
                    set_err(err, errn, "%s: an import list is outside the image", path);
                    goto fail;
                }
                uint64_t ent = rd64(t);
                if (!ent) break;
                if (!(ent >> 63) && (!IN_IMG((uint32_t)ent, 3) || !memchr(img + (uint32_t)ent + 2, 0, size - (uint32_t)ent - 2))) {
                    set_err(err, errn, "%s: an import's name is outside the image", path);
                    goto fail;
                }
                nimp++;
            }
        }
        mod->stubs_size = ((nimp * 32) + 0xFFF) & ~(size_t)0xFFF;
        if (mod->stubs_size) {
            mod->stubs = mmap(NULL, mod->stubs_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (mod->stubs == MAP_FAILED) { mod->stubs = NULL; set_err(err, errn, "%s: no memory for the import traps", path); goto fail; }
        }
        size_t k = 0;
        for (uint8_t *d = img + rd32(dirs + 8); rd32(d + 12); d += 20) {
            const char *dll = (const char *)img + rd32(d + 12);
            uint8_t *names = img + (rd32(d) ? rd32(d) : rd32(d + 16));
            uint8_t *iat = img + rd32(d + 16);
            for (size_t j = 0; rd64(names + 8 * j); j++) {
                uint64_t ent = rd64(names + 8 * j);
                char nm[300];
                void *fn = NULL;
                if (ent >> 63) snprintf(nm, sizeof nm, "#%u", (unsigned)(ent & 0xFFFF));
                else {
                    snprintf(nm, sizeof nm, "%s", (const char *)img + (uint32_t)ent + 2);
                    fn = shim_lookup(dll, nm);
                }
                if (!fn) {
                    char full[600];
                    snprintf(full, sizeof full, "%s!%s", dll, nm);
                    pthread_mutex_lock(&g_unres_lock);
                    int idx = -1;
                    for (int u = 0; u < g_nunresolved; u++)
                        if (!strcmp(g_unresolved[u], full)) { idx = u; break; }
                    if (idx < 0 && g_nunresolved < (int)(sizeof g_unresolved / sizeof *g_unresolved)) {
                        idx = g_nunresolved;
                        g_unresolved[g_nunresolved++] = strdup(full);
                    }
                    pthread_mutex_unlock(&g_unres_lock);
                    emit_trap(mod->stubs + 32 * k, idx < 0 ? 0xFFFFFFFF : (uint32_t)idx);
                    fn = mod->stubs + 32 * k;
                }
                memcpy(iat + 8 * j, &fn, 8);
                k++;
            }
        }
        if (mod->stubs) mprotect(mod->stubs, mod->stubs_size, PROT_READ | PROT_EXEC);
    }

    /* section protections (an image aligned below the page size keeps one RWX mapping) */
    if (salign >= 0x1000) {
        mprotect(img, (hdrsz + 0xFFF) & ~0xFFFu, PROT_READ);
        for (unsigned i = 0; i < nsec; i++) {
            const uint8_t *s = sec + 40 * i;
            uint32_t vsz = rd32(s + 8), va = rd32(s + 12), rsz = rd32(s + 16), ch = rd32(s + 36);
            uint32_t n = vsz ? vsz : rsz;
            if (!n) continue;
            int prot = 0;                   /* (discardable sections, the DWARF debug info, stay readable) */
            if (ch & 0x40000000) prot |= PROT_READ;
            if (ch & 0x80000000) prot |= PROT_WRITE;
            if (ch & 0x20000000) prot |= PROT_EXEC;
            if (!prot) prot = PROT_READ;
            mprotect(img + va, ((size_t)n + 0xFFF) & ~(size_t)0xFFF, prot);
        }
    } else {
        mprotect(img, size, PROT_READ | PROT_WRITE | PROT_EXEC);
    }

    munmap(f, fsz);
    pthread_mutex_lock(&g_mods_lock);
    mod->next = g_mods;
    g_mods = mod;
    pthread_mutex_unlock(&g_mods_lock);
    return img;

fail:
    if (mod) {
        if (mod->stubs) munmap(mod->stubs, mod->stubs_size);
        free(mod->exports);
        free(mod);
    }
    if (img) munmap(img, size);
    munmap(f, fsz);
    return NULL;
}

/* Unloads a module loaded by wl_load (FreeLibrary). Returns 1, or 0 when base is not one. */
EXPORT int wl_free(void *base) {
    pthread_mutex_lock(&g_mods_lock);
    wl_module **pp = &g_mods, *m = NULL;
    for (; *pp; pp = &(*pp)->next)
        if ((*pp)->base == base) { m = *pp; *pp = m->next; break; }
    pthread_mutex_unlock(&g_mods_lock);
    if (!m) return 0;
    if (m->stubs) munmap(m->stubs, m->stubs_size);
    free(m->exports);
    munmap(m->base, m->size);
    free(m);
    return 1;
}

/* the address of an export, or NULL */
EXPORT void *wl_sym(void *base, const char *name) {
    wl_module *m = wl_find_module_by_addr((uintptr_t)base);
    return m && m->base == base ? wl_module_sym(m, name) : NULL;
}

/* the base of the loaded PE image holding addr, or NULL (a native function then) */
EXPORT void *wl_module_of(void *addr) {
    wl_module *m = wl_find_module_by_addr((uintptr_t)addr);
    return m ? m->base : NULL;
}

/* the shim an import is bound to (NULL when it would be a trap): for winload_abi_check.py, which calls them the
   Windows way and checks the registers Windows expects kept */
EXPORT void *wl_shim(const char *name) { return shim_lookup(NULL, name); }

/* the names of the imports bound to a trap so far (one per line), for the doctor and the tests */
EXPORT int wl_unresolved(char *out, size_t n) {
    size_t k = 0;
    pthread_mutex_lock(&g_unres_lock);
    for (int i = 0; i < g_nunresolved; i++) {
        int w = snprintf(out + k, k < n ? n - k : 0, "%s\n", g_unresolved[i]);
        if (w > 0) k += (size_t)w;
    }
    pthread_mutex_unlock(&g_unres_lock);
    if (n) out[k < n ? k : n - 1] = 0;
    return g_nunresolved;
}

/* VirtualProtect for the tools that patch code (st_patches.py): Windows protection constant in, the old one out */
EXPORT int wl_protect(void *a, size_t n, uint32_t prot, uint32_t *old) { return k_VirtualProtect(a, n, prot, old); }

/* VirtualAlloc (st_romhack.py's trampolines near the image: an exact address, or NULL when taken) */
EXPORT void *wl_valloc(void *addr, size_t n, uint32_t type, uint32_t prot) { return k_VirtualAlloc(addr, n, type, prot); }
