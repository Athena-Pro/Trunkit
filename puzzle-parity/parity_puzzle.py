#!/usr/bin/env python3
"""
Sliding-puzzle solvability as a PERMUTATION-PARITY feature — shaped for Trunkit.

Algebraic feature (the whole puzzle reduces to this one number):
    I(state) = sign(perm of tiles relative to goal) * (-1)^(Manhattan dist of blank from its goal cell)
Every legal slide is a transposition (flips the sign) that also moves the blank one
step (flips the (-1)^dist factor), so I is INVARIANT under legal play. The goal has
I = +1, therefore:  state is solvable  <=>  I(state) == +1.

This gives Trunkit its three verdicts cleanly:
    valid       — a witness (move sequence) is supplied and the kernel re-plays it to the goal
    refuted     — I(state) == -1: NO witness can exist; the parity computation IS the certificate
                  (also: a supplied witness that fails to reach the goal)
    unverified  — I(state) == +1 (solvable) but no witness has been attached yet

Trunkit mapping:
    curry  fn   puzzle_check(state, goal, moves) -> bool        (pure; this file's kernel_verify)
    claim       "state S is solvable"  method = struct_kan (the parity invariant)
    witness     the move sequence       method = witness_carry
    kernel      kernel_verify re-plays the moves  (mirrors trunkit.kernel_verify)
"""
from collections import deque
from itertools import permutations
import random

def perm_sign(perm):
    n=len(perm); seen=[False]*n; s=1
    for i in range(n):
        if not seen[i]:
            j=i; L=0
            while not seen[j]:
                seen[j]=True; j=perm[j]; L+=1
            if L%2==0: s=-s
    return s

class Puzzle:
    def __init__(self, R, C):
        self.R, self.C, self.N = R, C, R*C
        self.goal = tuple(list(range(1, self.N)) + [0])

    def feature(self, st):
        pos = {v:i for i,v in enumerate(self.goal)}
        sg = perm_sign([pos[v] for v in st])
        bi, gi = st.index(0), self.goal.index(0)
        dist = abs(bi//self.C - gi//self.C) + abs(bi%self.C - gi%self.C)
        return sg * (1 if dist%2==0 else -1)

    def solvable(self, st):
        return self.feature(st) == 1

    def _neighbors(self, bi):
        r,c = divmod(bi, self.C); out=[]
        for dr,dc,mv in ((-1,0,'U'),(1,0,'D'),(0,-1,'L'),(0,1,'R')):
            nr,nc = r+dr, c+dc
            if 0<=nr<self.R and 0<=nc<self.C: out.append((nr*self.C+nc, mv))
        return out

    def apply_move(self, st, mv):
        bi = st.index(0); r,c = divmod(bi, self.C)
        dr,dc = {'U':(-1,0),'D':(1,0),'L':(0,-1),'R':(0,1)}[mv]
        nr,nc = r+dr, c+dc
        if not (0<=nr<self.R and 0<=nc<self.C): return None
        ni = nr*self.C+nc; lst=list(st)
        lst[bi],lst[ni] = lst[ni],lst[bi]
        return tuple(lst)

    def kernel_verify(self, st, moves):
        """The kernel: re-play a witness, return a three-valued verdict."""
        cur = st
        for k,mv in enumerate(moves):
            cur = self.apply_move(cur, mv)
            if cur is None:
                return ("refuted", f"illegal move #{k+1} '{mv}'")
        if cur == self.goal:
            return ("valid", f"reaches goal in {len(moves)} moves")
        return ("refuted", "witness does not reach goal")

    def claim_check(self, st, witness=None):
        """Mirror of trunkit.claim_check for the claim 'st is solvable'."""
        if witness is not None:
            return self.kernel_verify(st, witness)
        if not self.solvable(st):
            return ("refuted", f"parity invariant I={self.feature(st)} (must be +1) — unsolvable, no witness exists")
        return ("unverified", "solvable by parity, but no witness attached")

    def solve(self, st):
        """Produce a witness (BFS — intended for small boards like 3x3)."""
        if st == self.goal: return []
        seen={st}; q=deque([(st,[])])
        while q:
            cur,path = q.popleft()
            for ni,mv in self._neighbors(cur.index(0)):
                lst=list(cur); bi=cur.index(0)
                lst[bi],lst[ni]=lst[ni],lst[bi]; nx=tuple(lst)
                if nx in seen: continue
                if nx==self.goal: return path+[mv]
                seen.add(nx); q.append((nx,path+[mv]))
        return None

    def scramble(self, k, seed=0):
        rng=random.Random(seed); st=self.goal
        for _ in range(k):
            opts=self._neighbors(st.index(0))
            ni,mv=rng.choice(opts); bi=st.index(0)
            lst=list(st); lst[bi],lst[ni]=lst[ni],lst[bi]; st=tuple(lst)
        return st


def prove_invariant_equals_reachability():
    """8-puzzle: BFS-reachable set must equal the invariant-positive set."""
    p=Puzzle(3,3)
    seen={p.goal}; q=deque([p.goal])
    while q:
        cur=q.popleft()
        for ni,mv in p._neighbors(cur.index(0)):
            lst=list(cur); bi=cur.index(0)
            lst[bi],lst[ni]=lst[ni],lst[bi]; nx=tuple(lst)
            if nx not in seen: seen.add(nx); q.append(nx)
    positive={perm for perm in permutations(range(9)) if p.feature(perm)==1}
    return len(seen), len(positive), seen==positive


if __name__ == "__main__":
    print("="*66)
    print("PROOF: parity feature == reachability  (8-puzzle, all 9! states)")
    reach, pos, eq = prove_invariant_equals_reachability()
    print(f"  BFS-reachable from goal : {reach}")
    print(f"  invariant-positive set  : {pos}")
    print(f"  sets identical          : {eq}   (9!/2 = {362880//2})")

    p = Puzzle(3,3)
    print("\n" + "="*66)
    print("THREE VERDICTS (8-puzzle)")

    s = p.scramble(40, seed=7)
    print(f"\n[A] solvable scramble {s}")
    print(f"    feature I = {p.feature(s)}  ->  claim_check(no witness): {p.claim_check(s)}")
    w = p.solve(s)
    print(f"    solver witness ({len(w)} moves): {''.join(w)}")
    print(f"    claim_check(with witness): {p.claim_check(s, w)}")

    bad = w[:-2] if len(w)>2 else ['U']
    print(f"\n[B] same instance, WRONG witness {''.join(bad)}")
    print(f"    claim_check(bad witness): {p.claim_check(s, bad)}")

    unsolv = list(p.goal); unsolv[0],unsolv[1]=unsolv[1],unsolv[0]; unsolv=tuple(unsolv)
    print(f"\n[C] unsolvable instance {unsolv}  (two tiles swapped)")
    print(f"    feature I = {p.feature(unsolv)}  ->  claim_check: {p.claim_check(unsolv)}")

    print("\n" + "="*66)
    print("15-PUZZLE (4x4): instant parity verdict, NO search")
    P=Puzzle(4,4)
    s15=P.scramble(200, seed=3)
    print(f"  scramble feature I={P.feature(s15)} -> {'solvable' if P.solvable(s15) else 'unsolvable'}")
    u=list(P.goal); u[0],u[1]=u[1],u[0]; u=tuple(u)
    print(f"  goal with two tiles swapped: I={P.feature(u)} -> {P.claim_check(u)}")
    # sanity: invariant constant under 5000 random legal moves
    st=s15; f0=P.feature(st); ok=True
    r=random.Random(1)
    for _ in range(5000):
        ni,mv=r.choice(P._neighbors(st.index(0))); st=P.apply_move(st,mv)
        if P.feature(st)!=f0: ok=False; break
    print(f"  invariant constant over 5000 random legal moves: {ok}")
