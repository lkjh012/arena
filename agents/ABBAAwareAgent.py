from __future__ import annotations

from agents.base_agent import MaehwaAgent
from engine.game_state import GameState
from engine.position import Position


# ─────────────────────────────────────────────
# ABBAAwareAgent
#
# 스네이크 오더:
# A B B A A B B A ...
#
# 핵심 전략:
# - 적 연속행동 위험 회피
# - focus fire
# - 막타 우선
# - DMR 거리 유지
# - 고지대 활용
# - 거점 유지
#
# API 스펙 완전 준수:
# - enemy.action 절대 사용 안 함
# - 읽기 전용 객체 수정 없음
# - 표준 라이브러리만 사용
# ─────────────────────────────────────────────


# ─── objective ───
W_CAPTURE_STAND = 24.0
W_CAPTURE_DIST = -1.2
W_HIGH_GROUND = 10.0

# ─── offense ───
W_DAMAGE = 16.0
W_KILL = 75.0
W_FOCUS = 22.0

# ─── defense ───
W_INCOMING_DAMAGE = -9.0
W_LETHAL_EXPOSURE = -120.0

# ─── tactical ───
W_DMR_TOO_CLOSE = -28.0
W_MEDIC_NEAR = 4.0

TYPE_MULT = {
    ("shield", "dmr"): 1.5,
    ("rifle", "shield"): 1.5,
    ("dmr", "rifle"): 1.5,
}

HIGH_ATK_MULT = 1.10
MIN_DAMAGE = 1


class ABBAAwareAgent(MaehwaAgent):

    def on_game_start(self, game_state: GameState) -> None:
        self.focus_targets: dict[int, int] = {}

    # ─────────────────────────────────────────
    # main
    # ─────────────────────────────────────────

    def execute_phase(self, game_state: GameState) -> None:

        self.focus_targets.clear()

        units = [
            u for u in game_state.my_units
            if u.is_alive
        ]

        # 딜러 우선 행동
        priority = {
            "dmr": 0,
            "rifle": 1,
            "shield": 2,
            "medic": 3,
        }

        units.sort(
            key=lambda u: (
                priority.get(u.unit_class, 99),
                u.hp,
                u.unit_id,
            )
        )

        for unit in units:

            if unit.unit_class == "medic":
                self._handle_medic(unit, game_state)
            else:
                self._handle_combat(unit, game_state)

    # ─────────────────────────────────────────
    # combat
    # ─────────────────────────────────────────

    def _handle_combat(self, unit, gs: GameState) -> None:

        enemies = [
            e for e in gs.enemy_units
            if e.is_alive
        ]

        if not enemies:
            return

        reachable = list(unit.action.reachable_tiles())

        if unit.position not in reachable:
            reachable.append(unit.position)

        candidates = []

        for tile in reachable:

            base = self._position_score(
                unit,
                tile,
                gs,
            )

            # 이동만
            candidates.append(
                (
                    tile,
                    "wait",
                    -1,
                    base,
                )
            )

            # 이동 후 공격
            for enemy in enemies:

                if not unit.action.can_attack_from(
                    tile,
                    enemy,
                ):
                    continue

                dmg = self._estimate_damage(
                    unit,
                    enemy,
                    tile,
                    gs,
                )

                score = base

                # 데미지 가치
                score += dmg * W_DAMAGE

                # 막타 우선
                if dmg >= enemy.hp:
                    score += W_KILL

                # focus fire
                score += (
                    self.focus_targets.get(
                        enemy.unit_id,
                        0,
                    ) * W_FOCUS
                )

                # DMR 거리 유지
                if unit.unit_class == "dmr":

                    dist = gs.map.distance(
                        tile,
                        enemy.position,
                    )

                    if dist <= 2:
                        score += W_DMR_TOO_CLOSE

                candidates.append(
                    (
                        tile,
                        "attack",
                        enemy.unit_id,
                        score,
                    )
                )

        if not candidates:
            return

        best = max(
            candidates,
            key=lambda c: (
                c[3],
                c[0] == unit.position,
                -c[0].col,
                -c[0].row,
                -c[2],
            ),
        )

        tile, action, target_id, _ = best

        if tile != unit.position:
            unit.action.move_to(tile)

        if gs.is_first_round:
            return

        if action == "attack":

            target = gs.get_unit_by_id(target_id)

            if (
                target is not None
                and target.is_alive
                and unit.action.can_attack(target)
            ):

                unit.action.attack(target)

                self.focus_targets[target_id] = (
                    self.focus_targets.get(
                        target_id,
                        0,
                    ) + 1
                )

    # ─────────────────────────────────────────
    # medic
    # ─────────────────────────────────────────

    def _handle_medic(self, medic, gs: GameState) -> None:

        wounded = [
            u for u in gs.my_units
            if (
                u.is_alive
                and u.hp < u.max_hp
                and u.unit_id != medic.unit_id
            )
        ]

        if wounded:

            target = min(
                wounded,
                key=lambda u: (
                    u.hp / u.max_hp,
                    u.hp,
                    u.unit_id,
                )
            )

            medic.action.move_toward(
                target.position
            )

            if (
                not gs.is_first_round
                and medic.action.can_heal(target)
            ):
                medic.action.heal(target)

            return

        reachable = list(
            medic.action.reachable_tiles()
        )

        if medic.position not in reachable:
            reachable.append(medic.position)

        if not reachable:
            return

        best_tile = max(
            reachable,
            key=lambda t: self._position_score(
                medic,
                t,
                gs,
            )
        )

        if best_tile != medic.position:
            medic.action.move_to(best_tile)

    # ─────────────────────────────────────────
    # position scoring
    # ─────────────────────────────────────────

    def _position_score(
        self,
        unit,
        tile: Position,
        gs: GameState,
    ) -> float:

        gm = gs.map

        score = 0.0

        caps = gm.capture_point_positions

        # 거점 점령
        if tile in caps:
            score += W_CAPTURE_STAND

        # 거점 접근
        dist_cap = min(
            gm.distance(tile, cp)
            for cp in caps
        )

        score += dist_cap * W_CAPTURE_DIST

        # 고지
        if gm.is_high_ground(tile):
            score += W_HIGH_GROUND

        # medic 근처 유지
        if unit.unit_class != "medic":

            medics = [
                u for u in gs.my_units
                if (
                    u.is_alive
                    and u.unit_class == "medic"
                )
            ]

            if medics:

                near = min(
                    gm.distance(
                        tile,
                        m.position,
                    )
                    for m in medics
                )

                score += (
                    max(0, 4 - near)
                    * W_MEDIC_NEAR
                )

        # ─────────────────────────
        # 핵심:
        # 스네이크 오더 대응 위험 계산
        # ─────────────────────────

        incoming = self._expected_incoming_damage(
            tile,
            unit,
            gs,
        )

        score += (
            incoming
            * W_INCOMING_DAMAGE
        )

        # 연속 행동에 죽는 자리
        if incoming >= unit.hp:
            score += W_LETHAL_EXPOSURE

        return score

    # ─────────────────────────────────────────
    # ABBA 대응 핵심
    # enemy.action 사용 금지 버전
    # ─────────────────────────────────────────

    def _expected_incoming_damage(
        self,
        tile: Position,
        unit,
        gs: GameState,
    ) -> int:

        gm = gs.map

        total = 0

        for enemy in gs.enemy_units:

            if not enemy.is_alive:
                continue

            # enemy.action 없이 계산
            max_reach = enemy.mov + enemy.rng

            dist = gm.distance(
                enemy.position,
                tile,
            )

            # DMR 최소사거리 고려
            if enemy.unit_class == "dmr":
                if dist <= enemy.min_rng:
                    continue

            # 이동 후 공격 가능 예상
            if dist <= max_reach:

                dmg = self._estimate_damage(
                    enemy,
                    unit,
                    enemy.position,
                    gs,
                )

                total += dmg

        # ABBA 스네이크 오더 보정
        return int(total * 1.35)

    # ─────────────────────────────────────────
    # damage estimate
    # ─────────────────────────────────────────

    def _estimate_damage(
        self,
        attacker,
        defender,
        from_pos: Position,
        gs: GameState,
    ) -> int:

        type_mul = TYPE_MULT.get(
            (
                attacker.unit_class,
                defender.unit_class,
            ),
            1.0,
        )

        terrain_mul = (
            HIGH_ATK_MULT
            if gs.map.is_high_ground(from_pos)
            else 1.0
        )

        base = (
            attacker.atk
            * type_mul
            * terrain_mul
        )

        return max(
            MIN_DAMAGE,
            int(base) - defender.defense,
        )


AGENT_CLASS = ABBAAwareAgent