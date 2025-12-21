import sys
import re

def to_bin(val, bits):
    val = int(val)
    if val < 0:
        val = (1 << bits) + val
    return f"{val:0{bits}b}"

# Register map
regs = {f"x{i}": i for i in range(32)}
regs["zero"] = 0
regs["ra"] = 1
regs["sp"] = 2
regs["gp"] = 3
regs["tp"] = 4
regs["t0"] = 5
regs["t1"] = 6
regs["t2"] = 7
regs["s0"] = 8
regs["fp"] = 8
regs["s1"] = 9
regs["a0"] = 10
regs["a1"] = 11
regs["a2"] = 12
regs["a3"] = 13
regs["a4"] = 14
regs["a5"] = 15
regs["a6"] = 16
regs["a7"] = 17
regs["s2"] = 18
regs["s3"] = 19
regs["s4"] = 20
regs["s5"] = 21
regs["s6"] = 22
regs["s7"] = 23
regs["s8"] = 24
regs["s9"] = 25
regs["s10"] = 26
regs["s11"] = 27
regs["t3"] = 28
regs["t4"] = 29
regs["t5"] = 30
regs["t6"] = 31

def parse_reg(s):
    s = s.strip().replace(",", "")
    if s in regs:
        return regs[s]
    raise ValueError(f"Unknown register: {s}")

def parse_imm(s, labels={}, pc=0):
    s = s.strip().replace(",", "")
    if s in labels:
        offset = labels[s] - pc
        return offset
    try:
        return int(s, 0) # Handles 0x, 0b, decimal
    except:
        raise ValueError(f"Invalid immediate: {s}")

# Instructions
# opcode, funct3, funct7 (if R-type)
opcodes = {
    # R-type
    "add":  {"type": "R", "opcode": "0110011", "funct3": "000", "funct7": "0000000"},
    "sub":  {"type": "R", "opcode": "0110011", "funct3": "000", "funct7": "0100000"},
    "and":  {"type": "R", "opcode": "0110011", "funct3": "111", "funct7": "0000000"},
    "or":   {"type": "R", "opcode": "0110011", "funct3": "110", "funct7": "0000000"},
    "slt":  {"type": "R", "opcode": "0110011", "funct3": "010", "funct7": "0000000"},
    # I-type
    "addi": {"type": "I", "opcode": "0010011", "funct3": "000"},
    "lw":   {"type": "I_load", "opcode": "0000011", "funct3": "010"}, # special parsing for imm(rs1)
    "jalr": {"type": "I", "opcode": "1100111", "funct3": "000"}, # rs1, rd, imm ?? standard is jalr rd, rs1, imm
    # S-type
    "sw":   {"type": "S", "opcode": "0100011", "funct3": "010"},
    # B-type
    "beq":  {"type": "B", "opcode": "1100011", "funct3": "000"},
    "bne":  {"type": "B", "opcode": "1100011", "funct3": "001"},
    "blt":  {"type": "B", "opcode": "1100011", "funct3": "100"},
    # U-type
    "lui":  {"type": "U", "opcode": "0110111"},
    # J-type
    "jal":  {"type": "J", "opcode": "1101111"},
}

def assemble(lines):
    # Pass 1: Labels
    labels = {}
    pc = 0
    instructions = []
    
    for line in lines:
        line = line.split("#")[0].strip() # Remove comments
        if not line:
            continue
        if line.endswith(":"):
            labels[line[:-1]] = pc
            continue
        # Check for label at start of line "label: instr"
        if ":" in line:
            parts = line.split(":")
            labels[parts[0].strip()] = pc
            line = parts[1].strip()
            if not line: continue
            
        instructions.append((pc, line))
        pc += 4
        
    binary_lines = []
    
    for pc, line in instructions:
        parts = re.split(r'\s+', line)
        instr = parts[0]
        args = "".join(parts[1:]).split(",")
        # clean args
        args = [a.strip() for a in args]
        
        if instr not in opcodes:
            raise ValueError(f"Unknown instruction: {instr}")
        
        op = opcodes[instr]
        otype = op["type"]
        opcode = op["opcode"]
        
        bin_str = ""
        
        if otype == "R":
            # add rd, rs1, rs2
            rd = parse_reg(args[0])
            rs1 = parse_reg(args[1])
            rs2 = parse_reg(args[2])
            funct3 = op["funct3"]
            funct7 = op["funct7"]
            bin_str = f"{funct7}{to_bin(rs2,5)}{to_bin(rs1,5)}{funct3}{to_bin(rd,5)}{opcode}"
            
        elif otype == "I":
            # addi rd, rs1, imm
            # jalr rd, rs1, imm
            rd = parse_reg(args[0])
            rs1 = parse_reg(args[1])
            imm = parse_imm(args[2], labels, pc)
            funct3 = op["funct3"]
            bin_str = f"{to_bin(imm, 12)}{to_bin(rs1, 5)}{funct3}{to_bin(rd, 5)}{opcode}"
            
        elif otype == "I_load":
            # lw rd, imm(rs1)
            rd = parse_reg(args[0])
            # parse imm(rs1)
            if '(' in args[1] and args[1].endswith(')'):
                val_part, reg_part = args[1][:-1].split('(')
                imm = parse_imm(val_part, labels, pc)
                rs1 = parse_reg(reg_part)
            else:
                 raise ValueError(f"Invalid memory operand: {args[1]}")
            
            funct3 = op["funct3"]
            bin_str = f"{to_bin(imm, 12)}{to_bin(rs1, 5)}{funct3}{to_bin(rd, 5)}{opcode}"

        elif otype == "S":
            # sw rs2, imm(rs1)
            rs2 = parse_reg(args[0])
            if '(' in args[1] and args[1].endswith(')'):
                val_part, reg_part = args[1][:-1].split('(')
                imm = parse_imm(val_part, labels, pc)
                rs1 = parse_reg(reg_part)
            else:
                 raise ValueError(f"Invalid memory operand: {args[1]}")
            
            funct3 = op["funct3"]
            imm_bin = to_bin(imm, 12)
            bin_str = f"{imm_bin[:7]}{to_bin(rs2, 5)}{to_bin(rs1, 5)}{funct3}{imm_bin[7:]}{opcode}"
            
        elif otype == "B":
            # beq rs1, rs2, label/imm
            rs1 = parse_reg(args[0])
            rs2 = parse_reg(args[1])
            imm = parse_imm(args[2], labels, pc)
            funct3 = op["funct3"]
            imm_bin = to_bin(imm, 13) # 13 bits for branching (bit 0 is always 0)
            # imm structure: 12|10:5|4:1|11
            # bin string: imm[12], imm[10:5], rs2, rs1, funct3, imm[4:1], imm[11], opcode
            # imm_bin is 13 bits: [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
            # indicies:           0   1   2   3   4   5   6   7   8   9   10  11 12
            # bit 12 is imm_bin[0]
            # bit 11 is imm_bin[1]
            # bit 10:5 is imm_bin[2:8]
            # bit 4:1 is imm_bin[8:12]
            
            b12 = imm_bin[0]
            b11 = imm_bin[1]
            b10_5 = imm_bin[2:8]
            b4_1 = imm_bin[8:12]
            
            bin_str = f"{b12}{b10_5}{to_bin(rs2, 5)}{to_bin(rs1, 5)}{funct3}{b4_1}{b11}{opcode}"

        elif otype == "U":
            # lui rd, imm
            rd = parse_reg(args[0])
            imm = parse_imm(args[1], labels, pc)
            # imm is 20 bits (upper)
            # if user provides full 32 bit, we take upper? or user provides shifted?
            # Standard 'lui': rd = imm << 12.
            # Usually assembler takes the number and extracts top 20? 
            # Or user provides the 20 bit value?
            # Standard assembly: lui x1, 0x12345 -> x1 = 0x12345000.
            # The immediate field in instruction IS 0x12345.
            # So if input is 0x12345, we pack it directly.
            bin_str = f"{to_bin(imm, 20)}{to_bin(rd, 5)}{opcode}"
            
        elif otype == "J":
            # jal rd, label
            rd = parse_reg(args[0])
            imm = parse_imm(args[1], labels, pc)
            imm_bin = to_bin(imm, 21)
            # imm[20], imm[10:1], imm[11], imm[19:12]
            # imm_bin: 21 bits. [20 ... 0]
            # 20 is imm_bin[0]
            # 19:12 is imm_bin[1:9]
            # 11 is imm_bin[9] -> wait. 
            # indices: 0 (20), 1 (19), ... 8 (12), 9 (11), 10 (10) ... 19 (1), 20 (0)
            b20 = imm_bin[0]
            b19_12 = imm_bin[1:9]
            b11 = imm_bin[9]
            b10_1 = imm_bin[10:20]
            
            bin_str = f"{b20}{b10_1}{b11}{b19_12}{to_bin(rd, 5)}{opcode}"

        binary_lines.append(bin_str)
        
    return binary_lines

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python assembler.py <input.asm>")
        sys.exit(1)
        
    with open(sys.argv[1], "r") as f:
        lines = f.readlines()
        
    try:
        bins = assemble(lines)
        for b in bins:
            print(b)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
