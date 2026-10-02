namespace Drift
structure Cfg where
  n : Nat
def Strong (c : Cfg) : Prop := True
theorem target (c : Cfg) : Strong c := trivial
theorem helper : 1 = 1 := rfl
end Drift
