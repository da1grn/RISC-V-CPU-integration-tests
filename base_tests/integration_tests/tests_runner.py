import os
import subprocess
import re
import sys

# --- Configuration & Path Setup ---
# Get the directory where this script is located (integration_tests/)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Get the project root (one level up)
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Paths to tools and files
ASSEMBLER_SCRIPT = os.path.join(SCRIPT_DIR, "assembler.py")
TEMP_ASM_FILE = os.path.join(PROJECT_ROOT, "temp.asm")
INSTRUCTIONS_FILE = os.path.join(PROJECT_ROOT, "instructions.dat")
CPU_TEST_SOURCE = "cpu_test.v" # Relative to PROJECT_ROOT
CPU_TEST_EXE = "cpu_test"      # Relative to PROJECT_ROOT

# --- Test Cases ---
test_cases = [
    {
        "name": "Arithmetic",
        "asm": """
addi x1, x0, 10
addi x2, x0, 20
add x3, x1, x2
sub x4, x2, x1
and x5, x1, x2
or x6, x1, x2
slt x7, x1, x2
slt x8, x2, x1
lui x9, 1
addi x10, x0, -1
slt x11, x10, x0
        """,
        "checks": {
            "x1": 10, "x2": 20, "x3": 30, "x4": 10, "x5": 0, "x6": 30,
            "x7": 1, "x8": 0, "x9": 4096, "x10": 4294967295, "x11": 1
        }
    },
    {
        "name": "Memory",
        "asm": """
addi x1, x0, 100
sw x1, 0(x0)
lw x2, 0(x0)
addi x3, x0, 200
sw x3, 4(x0)
lw x4, 4(x0)
add x5, x2, x4
sw x5, 8(x0)
        """,
        "checks": {
            "x2": 100, "x4": 200, "x5": 300,
            "mem[0]": 100, "mem[4]": 200, "mem[8]": 300
        }
    },
    {
        "name": "Branch",
        "asm": """
addi x1, x0, 10
addi x2, x0, 10
beq x1, x2, label1
addi x3, x0, 1
label1:
addi x3, x0, 2
bne x1, x0, label2
addi x4, x0, 1
label2:
addi x4, x0, 2
addi x1, x0, 5
addi x2, x0, 10
blt x1, x2, label3
addi x5, x0, 1
label3:
addi x5, x0, 2
        """,
        "checks": {
            "x3": 2, "x4": 2, "x5": 2
        }
    },
    {
        "name": "Jump",
        "asm": """
jal x1, skip
addi x10, x0, 1
skip:
addi x2, x0, 2
addi x3, x0, 24
jalr x0, x3, 0
addi x4, x0, 1
pass:
addi x4, x0, 2
        """,
        "checks": {
            "x2": 2, "x4": 22, "x10": 0
        }
    },
    {
        "name": "LoopSum",
        "asm": """
# Sum numbers from 1 to 10. Result should be 55.
# x1 = counter (10)
# x2 = sum (0)
# x3 = decrement (-1)
addi x1, x0, 10
addi x2, x0, 0
addi x3, x0, -1
loop:
add x2, x2, x1
add x1, x1, x3
bne x1, x0, loop
        """,
        "checks": {
            "x2": 55, "x1": 0
        }
    },
    {
        "name": "SignedOps",
        "asm": """
# Test SLT and BLT with signed numbers
# x1 = 1
# x2 = -1 (0xFFFFFFFF)
addi x1, x0, 1
addi x2, x0, -1
# slt x3, x2, x1  -> (-1 < 1) ? 1 : 0  => 1
slt x3, x2, x1
# slt x4, x1, x2  -> (1 < -1) ? 1 : 0  => 0
slt x4, x1, x2
# blt test
blt x2, x1, is_less
addi x5, x0, 0
jal x0, skip_less
is_less:
addi x5, x0, 1
skip_less:
# inverse blt
blt x1, x2, is_less2
addi x6, x0, 0
jal x0, end
is_less2:
addi x6, x0, 1
end:
        """,
        "checks": {
            "x3": 1, "x4": 0, "x5": 1, "x6": 0
        }
    },
    {
        "name": "LargeConst",
        "asm": """
# Load 0x12345678
# lui loads upper 20 bits. 0x12345 << 12 = 0x12345000
# addi adds 12 bit signed immediate.
# 0x678 = 1656.
lui x1, 0x12345
addi x1, x1, 0x678
        """,
        "checks": {
            "x1": 305419896 # 0x12345678
        }
    },
    {
        "name": "FullInstructionSet",
        "asm": """
# Test ALL instructions in one flow
# 1. LUI & ADDI
lui x1, 0x10        # x1 = 0x10000 (65536)
addi x1, x1, 16     # x1 = 0x10010 (65552)

# 2. SW & LW
sw x1, 0(x0)        # mem[0] = 0x10010
lw x2, 0(x0)        # x2 = 0x10010

# 3. Arithmetic (ADD, SUB, AND, OR)
add x3, x1, x2      # x3 = 0x20020
sub x4, x3, x1      # x4 = 0x10010
and x5, x4, x1      # x5 = 0x10010
or x6, x5, x0       # x6 = 0x10010

# 4. SLT
addi x7, x0, 10
addi x8, x0, 20
slt x9, x7, x8      # x9 = 1 (10 < 20)
slt x10, x8, x7     # x10 = 0

# 5. Branches (BEQ, BNE, BLT)
beq x7, x7, b1_ok
addi x11, x0, 999   # Should skip
b1_ok:
addi x11, x0, 1     # x11 = 1

bne x7, x8, b2_ok
addi x12, x0, 999
b2_ok:
addi x12, x0, 1     # x12 = 1

blt x7, x8, b3_ok
addi x13, x0, 999
b3_ok:
addi x13, x0, 1     # x13 = 1

# 6. Jumps (JAL, JALR)
jal x15, func_call
addi x14, x0, 2     # Should be executed after return
jal x0, end

func_call:
addi x16, x0, 100
jalr x17, x15, 0    # Return, store link in x17 (don't trash x0)

end:
addi x0, x0, 0
        """,
        "checks": {
            "x1": 65552, "x2": 65552, "x3": 131104, "x4": 65552, 
            "x5": 65552, "x6": 65552, "x9": 1, "x10": 0,
            "x11": 1, "x12": 1, "x13": 1, "x14": 2, "x16": 100,
            "x17": 104 # Address of instruction after jalr (PC of jalr is 100 + 4 = 104? No. 
            # PC of jalr is 0x64 (100).
            # rd = PC + 4 = 104. Correct.
        }
    },
    {
        "name": "ComplexLogic",
        "asm": """
# Implements a collatz-like sequence step store
# if (x1 % 2 == 0) x1 = x1 / 2 else x1 = 3*x1 + 1 (simplified)
# Actually, let's just do a memory array fill loop with logic
# x1 = address ptr (0)
# x2 = counter (5)
# x3 = value (10)

addi x1, x0, 0
addi x2, x0, 5
addi x3, x0, 10

loop:
sw x3, 0(x1)        # mem[ptr] = value
addi x1, x1, 4      # ptr += 4
addi x3, x3, 10     # value += 10
addi x2, x2, -1     # counter--
bne x2, x0, loop    # if counter != 0 goto loop

# Now sum them back using a function call
addi x1, x0, 0      # ptr = 0
addi x2, x0, 5      # counter = 5
addi x10, x0, 0     # sum = 0

sum_loop:
lw x4, 0(x1)
jal x5, add_to_sum
addi x1, x1, 4
addi x2, x2, -1
bne x2, x0, sum_loop
jal x0, end

add_to_sum:
add x10, x10, x4
jalr x0, x5, 0

end:
        """,
        "checks": {
            # values stored: 10, 20, 30, 40, 50. Sum = 150.
            "mem[0]": 10, "mem[16]": 50, "x10": 150
        }
    }
]

def run_test(test):
    print(f"Running test: {test['name']}")
    
    # 1. Write ASM to temp file in project root
    with open(TEMP_ASM_FILE, "w") as f:
        f.write(test["asm"])
        
    # 2. Assemble
    try:
        # Run assembler, capture output
        asm_out = subprocess.check_output(["python3", ASSEMBLER_SCRIPT, TEMP_ASM_FILE], cwd=PROJECT_ROOT).decode()
        
        # Write to instructions.dat in project root
        with open(INSTRUCTIONS_FILE, "w") as f:
            f.write(asm_out)
    except subprocess.CalledProcessError as e:
        print(f"Assembler failed: {e.output}")
        return False

    # 3. Run Simulation
    try:
        # Compile cpu_test.v using iverilog
        # We run this inside PROJECT_ROOT so imports work correctly
        compile_cmd = ["iverilog", "-g2012", "-o", CPU_TEST_EXE, CPU_TEST_SOURCE]
        subprocess.check_call(compile_cmd, cwd=PROJECT_ROOT)
        
        # Run the executable
        # ./cpu_test
        run_cmd = [f"./{CPU_TEST_EXE}"]
        output = subprocess.check_output(run_cmd, stderr=subprocess.STDOUT, cwd=PROJECT_ROOT).decode()
    except subprocess.CalledProcessError as e:
        print(f"Simulation failed: {e.output}")
        return False
        
    # 4. Parse Output
    regs = {}
    mem = {}
    
    for line in output.splitlines():
        # Register:          0, value:          0
        m = re.match(r"Register:\s+(\d+), value:\s+(-?\d+)", line)
        if m:
            regs[f"x{m.group(1)}"] = int(m.group(2))
            continue
            
        # Addr:          0, value:          0
        m = re.match(r"Addr:\s+(\d+), value:\s+(-?\d+)", line)
        if m:
            mem[f"mem[{m.group(1)}]"] = int(m.group(2))
            
    # 5. Check Expectations
    failed = False
    for k, v in test["checks"].items():
        actual = 0
        if k in regs:
            actual = regs[k]
        elif k in mem:
            actual = mem[k]
        else:
            print(f"  Unknown check key: {k}")
            failed = True
            continue
            
        if actual != v:
            print(f"  Check failed: {k} expected {v}, got {actual}")
            failed = True
            
    if failed:
        print("  FAIL")
        print("  --- Debug Output ---")
        print(output)
        print("  --------------------")
    else:
        print("  PASS")
        
    return not failed

def cleanup():
    # Remove temp files if they exist
    if os.path.exists(TEMP_ASM_FILE):
        os.remove(TEMP_ASM_FILE)
    if os.path.exists(INSTRUCTIONS_FILE):
        os.remove(INSTRUCTIONS_FILE)

if __name__ == "__main__":
    success = True
    try:
        for t in test_cases:
            if not run_test(t):
                success = False
    finally:
        cleanup()
            
    if success:
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed.")
        sys.exit(1)