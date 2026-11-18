/* winload_abi_check.py's probe: calls fn(a0, a1, a2) with the Windows x64 convention after loading every register the
   Windows callee must keep (rbx rbp rdi rsi r12-r15, xmm6-15) with a known value, and returns the bits of those that
   came back changed (bit i = the i-th of: rbx rbp rdi rsi r12 r13 r14 r15 xmm6 .. xmm15). Built as its own small .so. */
#include <stdint.h>

uint64_t wl_abi_probe(void *fn, uint64_t a0, uint64_t a1, uint64_t a2);

#define V(n) "0x5EED0000000000" #n
__asm__(
    ".text\n"
    ".globl wl_abi_probe\n"
    ".type wl_abi_probe,@function\n"
    "wl_abi_probe:\n"
    "  push %rbx\n  push %rbp\n  push %r12\n  push %r13\n  push %r14\n  push %r15\n"
    "  sub $40, %rsp\n"                             /* 32 bytes of shadow space, rsp 16-aligned at the call */
    "  mov %rdi, %rax\n"                            /* SysV in: rdi fn, rsi a0, rdx a1, rcx a2 */
    "  mov %rcx, %r8\n"                             /* Windows out: rcx a0, rdx a1, r8 a2 */
    "  mov %rsi, %rcx\n"
    "  movabs $" V(10) ", %rbx\n  movabs $" V(11) ", %rbp\n  movabs $" V(12) ", %rdi\n  movabs $" V(13) ", %rsi\n"
    "  movabs $" V(14) ", %r12\n  movabs $" V(15) ", %r13\n  movabs $" V(16) ", %r14\n  movabs $" V(17) ", %r15\n"
    "  movabs $" V(20) ", %r11\n  movq %r11, %xmm6\n  movabs $" V(21) ", %r11\n  movq %r11, %xmm7\n"
    "  movabs $" V(22) ", %r11\n  movq %r11, %xmm8\n  movabs $" V(23) ", %r11\n  movq %r11, %xmm9\n"
    "  movabs $" V(24) ", %r11\n  movq %r11, %xmm10\n  movabs $" V(25) ", %r11\n  movq %r11, %xmm11\n"
    "  movabs $" V(26) ", %r11\n  movq %r11, %xmm12\n  movabs $" V(27) ", %r11\n  movq %r11, %xmm13\n"
    "  movabs $" V(28) ", %r11\n  movq %r11, %xmm14\n  movabs $" V(29) ", %r11\n  movq %r11, %xmm15\n"
    "  call *%rax\n"
    "  xor %eax, %eax\n"
#define CHK(reg, val, bit) "  movabs $" V(val) ", %r11\n  cmp %r11, " reg "\n  je 1f\n  or $" #bit ", %rax\n1:\n"
#define CHKX(x, val, bit) "  movq " x ", %r10\n  movabs $" V(val) ", %r11\n  cmp %r11, %r10\n  je 1f\n  or $" #bit ", %rax\n1:\n"
    CHK("%rbx", 10, 0x1) CHK("%rbp", 11, 0x2) CHK("%rdi", 12, 0x4) CHK("%rsi", 13, 0x8)
    CHK("%r12", 14, 0x10) CHK("%r13", 15, 0x20) CHK("%r14", 16, 0x40) CHK("%r15", 17, 0x80)
    CHKX("%xmm6", 20, 0x100) CHKX("%xmm7", 21, 0x200) CHKX("%xmm8", 22, 0x400) CHKX("%xmm9", 23, 0x800)
    CHKX("%xmm10", 24, 0x1000) CHKX("%xmm11", 25, 0x2000) CHKX("%xmm12", 26, 0x4000) CHKX("%xmm13", 27, 0x8000)
    CHKX("%xmm14", 28, 0x10000) CHKX("%xmm15", 29, 0x20000)
    "  add $40, %rsp\n"
    "  pop %r15\n  pop %r14\n  pop %r13\n  pop %r12\n  pop %rbp\n  pop %rbx\n"
    "  ret\n"
    ".size wl_abi_probe, .-wl_abi_probe\n");
