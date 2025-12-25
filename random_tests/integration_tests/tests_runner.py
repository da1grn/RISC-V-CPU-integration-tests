import os
import subprocess
import re
import sys
import random

# --- Configuration & Path Setup ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
ASSEMBLER_SCRIPT = os.path.join(SCRIPT_DIR, "assembler.py")
TEMP_ASM_FILE = os.path.join(PROJECT_ROOT, "temp.asm")
INSTRUCTIONS_FILE = os.path.join(PROJECT_ROOT, "instructions.dat")
CPU_TEST_SOURCE = "cpu_test.v"
CPU_TEST_EXE = "cpu_test"

# --- Reference Simulator (Hardware-Accurate) ---
class ReferenceCPU:
    def __init__(self):
        # Hardware-specific: x0 is writable in this implementation
        self.regs = [0] * 32
        # Memory is byte-addressable map, but we store 32-bit words at aligned addresses
        self.mem = {}

    def _to_unsigned_32(self, val):
        """Truncate arbitrary python int to 32-bit unsigned."""
        return val & 0xFFFFFFFF

    def _to_signed_32(self, val):
        """Interpret 32-bit unsigned val as 32-bit signed integer."""
        val = val & 0xFFFFFFFF
        if val & 0x80000000:
            return val - 0x100000000
        return val

    def _sign_extend_12(self, imm12):
        """Sign extend 12-bit immediate to 32-bit signed."""
        imm12 = imm12 & 0xFFF
        if imm12 & 0x800:
            return imm12 - 0x1000
        return imm12

    def set_reg(self, idx, val):
        self.regs[idx] = self._to_unsigned_32(val)

    def get_reg(self, idx):
        return self.regs[idx]

    def store_word(self, addr, val):
        # Align to 4 bytes
        aligned_addr = (addr // 4) * 4
        self.mem[aligned_addr] = self._to_unsigned_32(val)

    def load_word(self, addr):
        aligned_addr = (addr // 4) * 4
        return self.mem.get(aligned_addr, 0)

    def execute(self, op, rd, rs1, rs2=None, imm=None):
        # Fetch Operands (Unsigned 32-bit storage)
        u_rs1 = self.get_reg(rs1)
        u_rs2 = self.get_reg(rs2) if rs2 is not None else 0
        
        # Prepare Signed interpretations for Arithmetic/Comparison
        s_rs1 = self._to_signed_32(u_rs1)
        s_rs2 = self._to_signed_32(u_rs2)
        
        # Prepare Immediate (Sign Extended)
        s_imm = 0
        if imm is not None:
            # Assume imm passed from generator is python int.
            # We treat it as 12-bit signed field.
            s_imm = self._to_unsigned_32(imm) # raw bits
            # If instruction uses signed extension (addi, lw, sw, branches)
            # Ops 'addi', 'lw', 'sw', 'jalr' use I/S type signed imm.
            # 'lui' uses U type (shifted).
            if op not in ['lui']:
                # Re-interpret input python int as 12-bit signed immediate behavior
                # Generator gives us usually correct python int, but let's be strict
                s_imm = imm # Generator gives -2048..2047 directly.
        
        res = 0
        
        if op == 'add':
            # Verilog: rs1 + rs2 (wraps)
            res = (u_rs1 + u_rs2)
        elif op == 'sub':
            # Verilog: rs1 - rs2 (wraps)
            res = (u_rs1 - u_rs2)
        elif op == 'and':
            res = u_rs1 & u_rs2
        elif op == 'or':
            res = u_rs1 | u_rs2
        elif op == 'slt':
            # Verilog: $signed(rs1) < $signed(rs2)
            res = 1 if s_rs1 < s_rs2 else 0
        elif op == 'addi':
            # Verilog: rs1 + imm (sign extended)
            # Python math handles signed addition correctly, just need to mask result
            res = u_rs1 + s_imm
        elif op == 'lui':
            # Verilog: {imm[19:0], 12'b0}
            # Generator passes 'imm' as the 20-bit value
            res = (imm & 0xFFFFF) << 12
        elif op == 'sw':
            # Verilog addr: rs1 + sext(imm)
            addr = (u_rs1 + s_imm) & 0xFFFFFFFF
            self.store_word(addr, u_rs2)
            return # No register writeback
        elif op == 'lw':
            # Verilog addr: rs1 + sext(imm)
            addr = (u_rs1 + s_imm) & 0xFFFFFFFF
            res = self.load_word(addr)
        
        # Commit result (mask to 32 bits)
        self.set_reg(rd, res)

# --- Fuzzing Generators ---

def random_imm12():
    # Full 12-bit range
    return random.randint(-2048, 2047)

def random_reg_idx():
    return random.randint(0, 31)

def gen_alu_chaos(count=30):
    """
    Generates random ALU operations including edge cases and full bit patterns.
    Now that Sim is HW-accurate, we can use complex inputs.
    """
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        
        # 1. Initialize with Edge Case Patterns
        patterns = [0, 1, -1, 0xAAAAAAAA, 0x55555555, 0x7FFFFFFF, 0x80000000, 12345, -9876]
        
        for i, pat in enumerate(patterns):
            reg_idx = (i % 31) + 1 # x1..x31
            
            # Load arbitrary 32-bit constant using LUI + ADDI
            val_u32 = pat & 0xFFFFFFFF
            upper_20 = (val_u32 >> 12) & 0xFFFFF
            lower_12 = val_u32 & 0xFFF
            
            # Compensation for ADDI sign-extension
            # If lower_12 top bit is 1, ADDI will subtract 4096. 
            # We must pre-add 1 to upper to compensate.
            if lower_12 & 0x800:
                upper_20 = (upper_20 + 1) & 0xFFFFF
                # ASM expects signed decimal for addi
                lower_12_asm = lower_12 - 4096
            else:
                lower_12_asm = lower_12
                
            sim.execute('lui', reg_idx, 0, imm=upper_20)
            asm.append(f"lui x{reg_idx}, {upper_20}")
            
            if lower_12_asm != 0:
                sim.execute('addi', reg_idx, reg_idx, imm=lower_12_asm)
                asm.append(f"addi x{reg_idx}, x{reg_idx}, {lower_12_asm}")

        # 2. Random Operations
        ops = ['add', 'sub', 'and', 'or', 'slt']
        
        # Execute EXACTLY ONE random operation to isolate faults
        for _ in range(1): 
            op = random.choice(ops)
            rd = random.randint(1, 15)
            rs1 = random.randint(0, 15)
            rs2 = random.randint(0, 15)
            
            sim.execute(op, rd, rs1, rs2)
            asm.append(f"{op} x{rd}, x{rs1}, x{rs2}")

        checks = {f"x{r}": sim.get_reg(r) for r in range(32) if sim.get_reg(r) != 0}
        tests.append({"name": f"ALU_Chaos_{test_id+1}", "asm": "\n".join(asm), "checks": checks})
    return tests

def gen_memory_walk(count=30):
    """
    Simulates a pointer moving around memory.
    Constrained only by physical memory array size (0..2048 words).
    """
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        
        ptr_reg = 1 
        data_reg = 2 
        
        # Start in middle of memory to allow neg offsets
        current_ptr = 1000 
        sim.execute('addi', ptr_reg, 0, imm=current_ptr)
        asm.append(f"addi x{ptr_reg}, x0, {current_ptr}")
        
        for _ in range(40):
            action = random.choice(['write', 'read', 'move'])
            
            if action == 'move':
                move_amt = random.randint(-50, 50) * 4
                # Check bounds (Verilog memory is usually 0..8192 bytes roughly)
                if 0 <= current_ptr + move_amt < 2000:
                    current_ptr += move_amt
                    sim.execute('addi', ptr_reg, ptr_reg, imm=move_amt)
                    asm.append(f"addi x{ptr_reg}, x{ptr_reg}, {move_amt}")
                continue

            offset = random.randint(-100, 100) * 4
            addr = (current_ptr + offset) & 0xFFFFFFFF
            
            # Valid memory range check
            if not (0 <= addr < 2040): 
                continue
                
            if action == 'write':
                val = random_imm12() 
                sim.execute('addi', data_reg, 0, imm=val)
                asm.append(f"addi x{data_reg}, x0, {val}")
                
                sim.execute('sw', 0, ptr_reg, data_reg, imm=offset)
                asm.append(f"sw x{data_reg}, {offset}(x{ptr_reg})")
                
            elif action == 'read':
                dest = random.randint(3, 31)
                sim.execute('lw', dest, ptr_reg, imm=offset)
                asm.append(f"lw x{dest}, {offset}(x{ptr_reg})")

        checks = {f"x{r}": sim.get_reg(r) for r in range(32) if sim.get_reg(r) != 0}
        # Check touched memory
        for addr, val in list(sim.mem.items())[:25]:
             checks[f"mem[{addr}]"] = val
            
        tests.append({"name": f"Mem_Walk_{test_id+1}", "asm": "\n".join(asm), "checks": checks})
    return tests

def gen_branch_maze(count=30):
    """
    Branching logic test. Restored to full complexity.
    """
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        acc_reg = 10
        asm.append(f"addi x{acc_reg}, x0, 0")
        
        # 15 blocks, random registers, full immediate range
        for block_idx in range(15): 
            r1 = random.randint(1, 15)
            r2 = random.randint(1, 15)
            v1 = random_imm12()
            v2 = random_imm12()
            
            sim.execute('addi', r1, 0, imm=v1)
            sim.execute('addi', r2, 0, imm=v2)
            asm.append(f"addi x{r1}, x0, {v1}")
            asm.append(f"addi x{r2}, x0, {v2}")
            
            b_type = random.choice(['beq', 'bne', 'blt'])
            
            # Python Truth Check
            u_v1 = sim.get_reg(r1)
            u_v2 = sim.get_reg(r2)
            
            taken = False
            if b_type == 'beq': taken = (u_v1 == u_v2)
            elif b_type == 'bne': taken = (u_v1 != u_v2)
            elif b_type == 'blt': 
                # Signed comparison
                taken = (sim._to_signed_32(u_v1) < sim._to_signed_32(u_v2))
            
            label_skip = f"skip_{block_idx}"
            
            asm.append(f"{b_type} x{r1}, x{r2}, {label_skip}")
            
            if not taken:
                sim.execute('addi', acc_reg, acc_reg, imm=1)
            
            asm.append(f"addi x{acc_reg}, x{acc_reg}, 1")
            asm.append(f"{label_skip}:")

        checks = {f"x{acc_reg}": sim.get_reg(acc_reg)}
        tests.append({"name": f"Maze_{test_id+1}", "asm": "\n".join(asm), "checks": checks})
    return tests

def gen_jal_pingpong(count=10):
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        sim.execute('addi', 10, 0, imm=0)
        asm.append("addi x10, x0, 0")
        vals = [random_imm12() for _ in range(3)]
        
        sim.execute('addi', 10, 10, imm=vals[0]) 
        sim.execute('addi', 10, 10, imm=vals[1]) 
        sim.execute('addi', 10, 10, imm=vals[2]) 
        
        asm.append("jal x0, label_a")
        
        asm.append("label_c:")
        asm.append(f"addi x10, x10, {vals[2]}")
        asm.append("jal x0, end")
        
        asm.append("label_b:")
        asm.append(f"addi x10, x10, {vals[1]}")
        asm.append("jal x0, label_c")
        
        asm.append("label_a:")
        asm.append(f"addi x10, x10, {vals[0]}")
        asm.append("jal x0, label_b")
        
        asm.append("end:")
        
        tests.append({"name": f"PingPong_{test_id+1}", "asm": "\n".join(asm), "checks": {"x10": sim.get_reg(10)}})
    return tests

def gen_loops(count=20):
    """
    Tests backward branching and loop execution.
    """
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        
        # Loop counter 5..10
        loop_cnt = random.randint(5, 10)
        cnt_reg = 5
        acc_reg = 6
        
        sim.execute('addi', cnt_reg, 0, imm=loop_cnt)
        sim.execute('addi', acc_reg, 0, imm=0)
        
        asm.append(f"addi x{cnt_reg}, x0, {loop_cnt}")
        asm.append(f"addi x{acc_reg}, x0, 0")
        asm.append("loop_start:")
        
        # Loop body: acc += 2, cnt -= 1
        asm.append(f"addi x{acc_reg}, x{acc_reg}, 2")
        asm.append(f"addi x{cnt_reg}, x{cnt_reg}, -1")
        
        # Simulate loop effect
        for _ in range(loop_cnt):
            sim.execute('addi', acc_reg, acc_reg, imm=2)
            sim.execute('addi', cnt_reg, cnt_reg, imm=-1)
            
        asm.append(f"bne x{cnt_reg}, x0, loop_start")
        
        tests.append({"name": f"Loop_{test_id+1}", "asm": "\n".join(asm), "checks": {f"x{acc_reg}": sim.get_reg(acc_reg), f"x{cnt_reg}": 0}})
    return tests

def gen_call_ret(count=20):
    """
    Tests function calls using JAL (call) and JALR (return).
    """
    tests = []
    for test_id in range(count):
        sim = ReferenceCPU()
        asm = []
        
        arg_reg = 10
        ret_reg = 11
        
        # Setup argument
        arg_val = random_imm12()
        sim.execute('addi', arg_reg, 0, imm=arg_val)
        asm.append(f"addi x{arg_reg}, x0, {arg_val}")
        
        # Call function
        # JAL ra, func_target
        asm.append("jal x1, func_target")
        
        # Simulation: The function adds 10 to arg and puts in ret
        sim.execute('addi', ret_reg, arg_reg, imm=10)
        
        # Main continues here
        asm.append("main_cont:")
        # We might do something else to prove we returned
        sim.execute('addi', ret_reg, ret_reg, imm=1)
        asm.append(f"addi x{ret_reg}, x{ret_reg}, 1")
        asm.append("jal x0, end_test")
        
        # Function definition
        asm.append("func_target:")
        asm.append(f"addi x{ret_reg}, x{arg_reg}, 10")
        # Return: jalr x0, x1, 0
        asm.append("jalr x0, x1, 0")
        
        asm.append("end_test:")
        
        # We don't check x1 (ra) because we can't predict PC easily
        tests.append({"name": f"CallRet_{test_id+1}", "asm": "\n".join(asm), "checks": {f"x{ret_reg}": sim.get_reg(ret_reg)}})
    return tests

# --- Main Runner ---

test_cases = []
print("Generating ALU Chaos tests...")
test_cases.extend(gen_alu_chaos(100))
print("Generating Memory Walk tests...")
test_cases.extend(gen_memory_walk(100))
print("Generating Branch Maze tests...")
test_cases.extend(gen_branch_maze(100))
print("Generating Jump PingPong tests...")
test_cases.extend(gen_jal_pingpong(50))
print("Generating Loop tests...")
test_cases.extend(gen_loops(50))
print("Generating Call/Ret tests...")
test_cases.extend(gen_call_ret(50))

def run_test(test):
    with open(TEMP_ASM_FILE, "w") as f:
        f.write(test["asm"])
        
    try:
        asm_out = subprocess.check_output(["python3", ASSEMBLER_SCRIPT, TEMP_ASM_FILE], cwd=PROJECT_ROOT).decode()
        with open(INSTRUCTIONS_FILE, "w") as f:
            f.write(asm_out)
    except subprocess.CalledProcessError as e:
        print(f"[{test['name']}] Assembler failed: {e.output}")
        return False

    try:
        run_cmd = [f"./{CPU_TEST_EXE}"]
        output = subprocess.check_output(run_cmd, stderr=subprocess.STDOUT, cwd=PROJECT_ROOT).decode()
    except subprocess.CalledProcessError as e:
        print(f"[{test['name']}] Simulation failed: {e.output}")
        return False
        
    regs = {}
    mem = {}
    
    for line in output.splitlines():
        m = re.match(r"Register:\s+(\d+), value:\s+(-?\d+)", line)
        if m:
            regs[f"x{m.group(1)}"] = int(m.group(2))
            continue
        m = re.match(r"Addr:\s+(\d+), value:\s+(-?\d+)", line)
        if m:
            mem[f"mem[{m.group(1)}]"] = int(m.group(2))
            
    failures = []
    for k, v in test["checks"].items():
        actual = 0
        if k in regs: actual = regs[k]
        elif k in mem: actual = mem[k]
        else:
            if v == 0: continue
            failures.append(f"[{test['name']}] Missing check key: {k} (Expected {v})")
            continue
            
        actual_u = actual & 0xFFFFFFFF
        expect_u = v & 0xFFFFFFFF
        
        if actual_u != expect_u:
            failures.append(f"[{test['name']}] Failed {k}: Expected {expect_u} (0x{expect_u:08X}), Got {actual_u} (0x{actual_u:08X})")
            
    if failures:
        print(f"\n========================================================")
        print(f"ERROR: Test [{test['name']}] FAILED")
        print(f"========================================================")
        print("Program Source Code:")
        print("--------------------------------------------------------")
        print(test['asm'])
        print("--------------------------------------------------------")
        print("Failures:")
        for fail in failures:
            print(fail)
        print("========================================================\n")
        return False

    return True

def compile_verilog():
    print("Compiling Verilog...")
    try:
        compile_cmd = ["iverilog", "-g2012", "-o", CPU_TEST_EXE, CPU_TEST_SOURCE]
        subprocess.check_call(compile_cmd, cwd=PROJECT_ROOT)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Compilation failed: {e}")
        return False

def cleanup():
    if os.path.exists(TEMP_ASM_FILE): os.remove(TEMP_ASM_FILE)
    if os.path.exists(INSTRUCTIONS_FILE): os.remove(INSTRUCTIONS_FILE)

if __name__ == "__main__":
    if not compile_verilog():
        sys.exit(1)
        
    total = len(test_cases)
    passed = 0
    
    print(f"Running {total} tests...")
    
    for t in test_cases:
        if run_test(t):
            passed += 1
        else:
            print(f"Test {t['name']} FAILED")
            
    print(f"\nFinished. {passed}/{total} passed.")
    cleanup()
    
    if passed == total:
        sys.exit(0)
    else:
        sys.exit(1)