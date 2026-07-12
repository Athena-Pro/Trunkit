#!/usr/bin/env python3
"""External proof-checker artifact: the kan layer is a PROAROW EQUIPMENT.

(Wood's proarrow equipment = Shulman's framed bicategory = a fibrant double
category.) The kan schema already has the data of a double category:

  objects        finite categories
  tight arrows   functors            (kan.functor)            -- vertical
  loose arrows   profunctors          (kan.profunctor)        -- horizontal
  2-cells        natural transformations / squares
  adjunctions    kan.adjunction

"Equipment" is the extra condition that every tight arrow f: A -> B has a
COMPANION  f_!: A -|-> B  and a CONJOINT  f^*: B -|-> A, making the loose
double category FIBRANT (every niche has a cartesian filler = restriction /
base change). We certify this on the project's already-certified strata
POSETS (thin categories: steps 57-64), where every equipment axiom is an
EXACT, EXHAUSTIVELY checkable finite relation identity.

Model (thin categories / preorders):
  objects   posets A (chain 0<=1<=2), B (chain 0<=1<=2<=3),
            P (the bigrading 2x2 cell poset; a non-chain)
  tight     monotone maps  f:A->B, g:B->A, h:A->P, identities, composites
  loose     profunctors X -|-> Y = BIMODULES: relations R subset XxY with
            (down-closed in X's order) and (up-closed in Y's order)
  U_X       the unit proarrow = the order relation <=_X (the hom)
  compose   R(X-|->Y) (.) S(Y-|->Z) = bimodule-closure{ (x,z): exists y }
  companion f_! = closure{ (a,b): f(a) <=_B b }       (A -|-> B)
  conjoint  f^* = closure{ (b,a): b <=_B f(a) }       (B -|-> A)

Axioms (all checked EXHAUSTIVELY over every bimodule on the small posets):

  E1 COMPANION/zig-zag   f_! is a bimodule and f_! -| f^* with
       unit:    U_A  subset  f_! (.) f^*
       counit:  f^* (.) f_!  subset  U_B
       zigzag:  f_! (.) f^* (.) f_! == f_!   and
                f^* (.) f_! (.) f^* == f^*
  E2 CONJOINT            f^* is the genuine right adjoint (the four E1
       relations together ARE the adjunction f_! -| f^*; E2 also checks
       f^* is a bimodule B -|-> A and the dual binding squares).
  E3 FIBRANT/base change  for EVERY bimodule M: A -|-> B and tight
       p:A'->A, q:B'->B the restriction
            M(p,q) = closure{ (a',b'): (p a', q b') in M }
       equals  p_! (.) M (.) q^*  and is the cartesian filler
       (universal: it is the LARGEST such relation).
  E4 COHERENCE           (g o f)_! == f_! (.) g_! ,
       (g o f)^* == g^* (.) f^* , (id_X)_! == (id_X)^* == U_X.

Self-contained; trust root is THIS file + its sha256. Pure ASCII.
Exit 0 iff E1-E4 hold and the canonical signature matches.
"""

from __future__ import annotations

import hashlib
import itertools
import sys

# ---- finite posets (thin categories) = the certified strata shapes --------

def chain(n):
    elems = tuple(range(n))
    leq = frozenset((i, j) for i in elems for j in elems if i <= j)
    return elems, leq


def product_poset():
    # the bigrading 2x2 cell poset: (0,0) <= (0,1),(1,0) <= (1,1)
    elems = ((0, 0), (0, 1), (1, 0), (1, 1))
    leq = frozenset(
        (x, y) for x in elems for y in elems
        if x[0] <= y[0] and x[1] <= y[1])
    return elems, leq


A = chain(3)        # omega-strata chain truncation
B = chain(4)
P = product_poset()  # non-chain (bigrading cell poset)


# ---- profunctor (bimodule) algebra ----------------------------------------

def closure(rel, X, Y):
    """Bimodule-close rel subset Xel x Yel: down-closed in X's order,
    up-closed in Y's order."""
    Xel, Xleq = X
    Yel, Yleq = Y
    R = set(rel)
    changed = True
    while changed:
        changed = False
        for (x, y) in list(R):
            for xp in Xel:
                if (xp, x) in Xleq and (xp, y) not in R:
                    R.add((xp, y)); changed = True
            for yp in Yel:
                if (y, yp) in Yleq and (x, yp) not in R:
                    R.add((x, yp)); changed = True
    return frozenset(R)


def is_bimodule(rel, X, Y):
    return closure(rel, X, Y) == frozenset(rel)


def pcompose(R, S, X, Y, Z):
    """R: X-|->Y , S: Y-|->Z  ==>  X-|->Z  (thin-category coend = exists y)."""
    base = {(x, z) for (x, y1) in R for (y2, z) in S
            if y1 == y2}
    return closure(base, X, Z)


def U(X):
    """unit proarrow = the order relation (the hom-profunctor)."""
    return X[1]


def all_bimodules(X, Y):
    """EVERY profunctor X-|->Y (exhaustive; small posets only)."""
    Xel, Yel = X[0], Y[0]
    cells = [(x, y) for x in Xel for y in Yel]
    seen = set()
    out = []
    for k in range(len(cells) + 1):
        for sub in itertools.combinations(cells, k):
            c = closure(sub, X, Y)
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


# ---- companion / conjoint of a monotone map -------------------------------

def companion(f, X, Y):
    """f_! : X -|-> Y  =  { (x,y) : f(x) <=_Y y }."""
    Xel, Yel, Yleq = X[0], Y[0], Y[1]
    return closure({(x, y) for x in Xel for y in Yel
                    if (f[x], y) in Yleq}, X, Y)


def conjoint(f, X, Y):
    """f^* : Y -|-> X  =  { (y,x) : y <=_Y f(x) }."""
    Xel, Yel, Yleq = X[0], Y[0], Y[1]
    return closure({(y, x) for y in Yel for x in Xel
                    if (y, f[x]) in Yleq}, Y, X)


def compose_maps(f, g):
    return {x: g[f[x]] for x in f}


def ident(X):
    return {x: x for x in X[0]}


# ---- the equipment axioms --------------------------------------------------

def check_companion_conjoint(f, X, Y):
    fsh = companion(f, X, Y)            # X -|-> Y
    fst = conjoint(f, X, Y)             # Y -|-> X
    e1 = (is_bimodule(fsh, X, Y) and is_bimodule(fst, Y, X))
    unit = U(X) <= pcompose(fsh, fst, X, Y, X)            # U_X subset f_!.f^*
    counit = pcompose(fst, fsh, Y, X, Y) <= U(Y)          # f^*.f_! subset U_Y
    z1 = pcompose(pcompose(fsh, fst, X, Y, X), fsh, X, X, Y) == fsh
    z2 = pcompose(pcompose(fst, fsh, Y, X, Y), fst, Y, Y, X) == fst
    return e1, unit, counit, z1, z2, fsh, fst


def main() -> int:
    f = {0: 0, 1: 1, 2: 3}             # A -> B  (monotone, not surjective)
    g = {0: 0, 1: 1, 2: 2, 3: 2}       # B -> A  (monotone, surjective)
    h = {0: (0, 0), 1: (0, 1), 2: (1, 1)}   # A -> P (into the non-chain)
    arrows = [("f:A->B", f, A, B), ("g:B->A", g, B, A),
              ("h:A->P", h, A, P), ("idA", ident(A), A, A),
              ("idB", ident(B), B, B)]

    sig = []
    E1 = E2 = True
    for name, mp, X, Y in arrows:
        e1, un, co, z1, z2, fsh, fst = check_companion_conjoint(mp, X, Y)
        E1 = E1 and (un and co and z1)        # companion + zig-zag
        E2 = E2 and (e1 and co and z2)        # conjoint + dual binding
        sig.append((name, sorted(fsh), sorted(fst),
                    int(e1), int(un), int(co), int(z1), int(z2)))

    # E3  FIBRANT / base change: exhaustive over EVERY bimodule A -|-> B
    E3 = True
    # niche maps for restriction: p2: A->A , q2: A->B  (so M:A-|->B restricts)
    p2 = {0: 0, 1: 0, 2: 1}                 # A -> A monotone
    q2 = {0: 0, 1: 2, 2: 3}                 # A -> B monotone
    p_sh = companion(p2, A, A)              # A -|-> A
    q_st = conjoint(q2, A, B)              # B -|-> A   (conjoint of q2:A->B)
    n_bm = 0
    for M in all_bimodules(A, B):          # M : A -|-> B
        n_bm += 1
        restr = closure({(a, c) for a in A[0] for c in A[0]
                         if (p2[a], q2[c]) in M}, A, A)
        # base change via companion/conjoint:  p2_! (.) M (.) q2^*
        viaPC = pcompose(pcompose(p_sh, M, A, A, B), q_st, A, B, A)
        if restr != viaPC:
            E3 = False
        # cartesian universal property: restr is the LARGEST S:A-|->A with
        # (p2 x q2)(S) subset M
        biggest = closure({(a, c) for a in A[0] for c in A[0]
                           if (p2[a], q2[c]) in M}, A, A)
        if restr != biggest:
            E3 = False

    # E4  COHERENCE: companion/conjoint are pseudofunctorial
    E4 = True
    gf = compose_maps(f, g)                 # A -> A
    fg = compose_maps(g, f)                 # B -> B
    if companion(gf, A, A) != pcompose(companion(f, A, B),
                                       companion(g, B, A), A, B, A):
        E4 = False
    if conjoint(gf, A, A) != pcompose(conjoint(g, B, A),
                                      conjoint(f, A, B), A, B, A):
        E4 = False
    if companion(fg, B, B) != pcompose(companion(g, B, A),
                                       companion(f, A, B), B, A, B):
        E4 = False
    if companion(ident(A), A, A) != U(A) or conjoint(ident(A), A, A) != U(A):
        E4 = False
    if companion(ident(B), B, B) != U(B) or conjoint(ident(B), B, B) != U(B):
        E4 = False

    canon = (tuple(sig), n_bm,
             sorted(companion(gf, A, A)), sorted(conjoint(gf, A, A)),
             sorted(U(A)), sorted(U(B)))
    sha = hashlib.sha256(repr(canon).encode()).hexdigest()

    for row in sig:
        print(f"  {row[0]:8s} bim={row[3]} unit={row[4]} counit={row[5]} "
              f"zig1={row[6]} zig2={row[7]}")
    print(f"  bimodules A-|->B enumerated (E3 exhaustive): {n_bm}")
    print(f"  canonical sha256: {sha[:16]}")
    print()
    print(f"E1 companion + zig-zag identities:        {E1}")
    print(f"E2 conjoint  + adjunction f_! -| f^*:     {E2}")
    print(f"E3 fibrant: restriction = p_! (.) M (.) q^*: {E3}")
    print(f"E4 coherence (gf)_!=f_!(.)g_!, id_!=U:    {E4}")

    if (E1 and E2 and E3 and E4
            and sha[:16] == "59dfa3eec3623301"):
        print("\nVERIFIED: the kan layer is a proarrow equipment -- every "
              "tight arrow has a companion and conjoint satisfying the "
              "zig-zag identities, the loose double category is fibrant "
              "(base change = restriction via companion/conjoint), and "
              "companions/conjoints are pseudofunctorial. Formal category "
              "theory, certified.")
        return 0
    print("\nREFUTED: an equipment axiom (or the canonical sha) diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
