from __future__ import annotations

from agents.base_agent import MaehwaAgent
from engine.game_state import GameState
from engine.position import Position


CAPS = (Position(3, 3), Position(5, 3), Position(7, 3))

TYPE_MULT = {
    ("shield", "dmr"): 1.5,
    ("rifle", "shield"): 1.5,
    ("dmr", "rifle"): 1.5,
}

HIGH_ATK_MULT = 1.10
MIN_DAMAGE = 1


class ABBAAwareAgent(MaehwaAgent):

    def on_game_start(self, game_state: GameState) -> None:
        self.focus_targets = {}

    def _safe(self, fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    # ─────────────────────────────
    # MAIN
    # ─────────────────────────────

    def execute_phase(self, game_state: GameState) -> None:

        self.focus_targets.clear()

        units = self._safe(
            lambda: [u for u in game_state.my_units if getattr(u, "is_alive", False)],
            []
        )

        priority = {"shield": 0, "rifle": 1, "dmr": 1, "medic": 2}

        units.sort(key=lambda u: (priority.get(u.unit_class, 99), u.hp, u.unit_id))

        for unit in units:

            try:
                if unit.unit_class == "shield":
                    self._shield(unit, game_state)

                elif unit.unit_class == "medic":
                    self._medic(unit, game_state)

                else:
                    self._move_attack(unit, game_state)

            except Exception:
                continue

    # ─────────────────────────────
    # SHIELD
    # ─────────────────────────────

    def _shield(self, unit, gs: GameState):
        self._safe(lambda: unit.action.move_toward(Position(5, 3)))

    # ─────────────────────────────
    # MEDIC (힐 최우선)
    # ─────────────────────────────

    def _medic(self, medic, gs: GameState):

        allies = self._safe(lambda: [u for u in gs.my_units if getattr(u, "is_alive", False)], [])

        wounded = [u for u in allies if u.hp < u.max_hp]

        if wounded:

            target = min(wounded, key=lambda u: u.hp)

            self._safe(lambda: medic.action.move_toward(target.position))

            if not gs.is_first_round:
                self._safe(lambda: medic.action.heal(target))

            return

        self._fallback_move(medic, gs)

    # ─────────────────────────────
    # MOVE + ATTACK INTEGRATED
    # ─────────────────────────────

    def _move_attack(self, unit, gs: GameState):

        enemies = self._safe(lambda: [e for e in gs.enemy_units if getattr(e, "is_alive", False)], [])
        if not enemies:
            self._fallback_move(unit, gs)
            return

        reachable = self._safe(lambda: list(unit.action.reachable_tiles()), [unit.position])

        if unit.position not in reachable:
            reachable.append(unit.position)

        best_tile = unit.position
        best_target = None
        best_score = -10**18

        for tile in reachable:

            # 이동 위치 기본 점수
            base = self._tile_score(tile, gs, enemies)

            # 공격 시뮬
            try:
                for e in enemies:

                    can_attack = unit.action.can_attack_from(tile, e)
                    if not can_attack:
                        continue

                    dmg = self._estimate_damage(unit, e, tile, gs)

                    score = base + dmg * 14

                    if dmg >= e.hp:
                        score += 70  # 킬 우선

                    if e.unit_id in self.focus_targets:
                        score += self.focus_targets[e.unit_id] * 20

                    if score > best_score:
                        best_score = score
                        best_tile = tile
                        best_target = e

            except Exception:
                continue

        # fallback (공격 없는 경우)
        if best_target is None:
            best_tile = max(reachable, key=lambda t: self._tile_score(t, gs, enemies))

        # 이동
        if best_tile != unit.position:
            self._safe(lambda: unit.action.move_to(best_tile))

        # 공격
        if best_target and not gs.is_first_round:
            self._safe(lambda: unit.action.attack(best_target))

            self.focus_targets[best_target.unit_id] = (
                self.focus_targets.get(best_target.unit_id, 0) + 1
            )

    # ─────────────────────────────
    # TILE SCORE (거점 중심)
    # ─────────────────────────────

    def _tile_score(self, t, gs, enemies):

        s = 0

        for cp in CAPS:
            try:
                d = gs.map.distance(t, cp)
                if d == 0:
                    s += 120
                elif d <= 2:
                    s += 60
                else:
                    s -= d * 2
            except Exception:
                pass

        try:
            s -= min(gs.map.distance(t, e.position) for e in enemies)
        except Exception:
            pass

        if gs.map.is_high_ground(t):
            s += 10

        return s

    # ─────────────────────────────
    # FALLBACK MOVE
    # ─────────────────────────────

    def _fallback_move(self, unit, gs):

        enemies = self._safe(lambda: [e for e in gs.enemy_units if getattr(e, "is_alive", False)], [])

        reachable = self._safe(lambda: list(unit.action.reachable_tiles()), [unit.position])

        def score(t):
            return self._tile_score(t, gs, enemies)

        best = max(reachable, key=score)

        self._safe(lambda: unit.action.move_to(best))

    # ─────────────────────────────
    # DAMAGE MODEL
    # ─────────────────────────────

    def _estimate_damage(self, attacker, defender, from_pos, gs):

        try:
            mul = TYPE_MULT.get((attacker.unit_class, defender.unit_class), 1.0)
            terrain = HIGH_ATK_MULT if gs.map.is_high_ground(from_pos) else 1.0

            base = attacker.atk * mul * terrain
            return max(MIN_DAMAGE, int(base) - defender.defense)

        except Exception:
            return MIN_DAMAGE


AGENT_CLASS = ABBAAwareAgent
