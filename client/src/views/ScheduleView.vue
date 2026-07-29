<template>
  <div class="schedule-page">
    <van-nav-bar title="对阵表" left-text="返回" left-arrow @click-left="$router.back()" />

    <div v-if="rounds.length === 0" class="empty-block">
      <van-empty description="暂无赛程" />
    </div>

    <template v-else>
      <div v-for="r in rounds" :key="r.id" class="round-section">
        <div class="round-header">
          <span class="round-label">第{{ r.round_number }}轮</span>
          <van-tag v-if="r.is_regenerated" type="warning" size="medium" round>重排</van-tag>
        </div>
        <div v-if="r.bye_player" class="bye-notice">
          <van-icon name="info-o" /> 轮空：{{ r.bye_player.username }}
        </div>

        <div v-for="m in r.matches" :key="m.id" class="match-card" :class="{ my: isMyMatch(m) }" @click="goScore(m)">
          <div class="match-teams">
            <div class="team" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
              <div class="team-pair">
                <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                  <div class="avatar-badge">
                    <van-image round width="36" height="36" :src="m.pairing_a.player_a.avatar || defaultAvatar" />
                    <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_a.id">🏆</span>
                  </div>
                  <span class="player-name">{{ m.pairing_a.player_a.username }}</span>
                </div>
                <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_a.id }">
                  <div class="avatar-badge">
                    <van-image round width="36" height="36" :src="m.pairing_a.player_b.avatar || defaultAvatar" />
                    <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_a.id">🏆</span>
                  </div>
                  <span class="player-name">{{ m.pairing_a.player_b.username }}</span>
                </div>
              </div>
            </div>

            <div class="score-box">
              <template v-if="m.status === 'finished'">
                <div class="score-row">{{ m.score_a }} : {{ m.score_b }}</div>
              </template>
              <template v-else-if="m.score_a != null || m.score_b != null">
                <span class="score-num">{{ m.score_a }}</span>
                <span class="score-divider">:</span>
                <span class="score-num">{{ m.score_b }}</span>
              </template>
              <span v-else class="vs-badge">VS</span>
            </div>

            <div class="team" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
              <div class="team-pair">
                <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
                  <div class="avatar-badge">
                    <van-image round width="36" height="36" :src="m.pairing_b.player_a.avatar || defaultAvatar" />
                    <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                  </div>
                  <span class="player-name">{{ m.pairing_b.player_a.username }}</span>
                </div>
                <div class="player-block" :class="{ win: m.winner_pairing_id === m.pairing_b.id }">
                  <div class="avatar-badge">
                    <van-image round width="36" height="36" :src="m.pairing_b.player_b.avatar || defaultAvatar" />
                    <span class="badge-icon" v-if="m.status === 'finished' && m.winner_pairing_id === m.pairing_b.id">🏆</span>
                  </div>
                  <span class="player-name">{{ m.pairing_b.player_b.username }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="match-foot">
            <span class="foot-info">{{ m.court_name || '' }}<template v-if="m.court_name && m.start_time"> · </template>{{ m.start_time?.slice(0, 8) || '' }}</span>
            <span v-if="m.referee" class="foot-ref has">裁 {{ m.referee.username }}</span>
            <van-button v-if="m.can_referee" size="mini" type="primary" round @click.stop="claimReferee(m.id)">认领</van-button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useWebSocket } from '@/composables/useWebSocket'
import { showToast } from 'vant'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const rounds = ref<any[]>([])
const defaultAvatar = 'https://img.yzcdn.cn/vant/cat.jpeg'

function isMyMatch(m: any) {
  if (!auth.user) return false
  const uid = auth.user.id
  return [m.pairing_a?.player_a?.id, m.pairing_a?.player_b?.id, m.pairing_b?.player_a?.id, m.pairing_b?.player_b?.id].includes(uid)
}

async function fetchRounds() {
  const res = await api.get(`/tournaments/${route.params.id}/rounds`)
  rounds.value = res.data
}

function goScore(m: any) {
  if (!auth.token) {
    sessionStorage.setItem('loginRedirect', `/tournament/${route.params.id}/schedule`)
    router.push('/profile')
    return
  }
  router.push(`/tournament/${route.params.id}/score/${m.id}`)
}

async function claimReferee(matchId: number) {
  await api.post(`/tournaments/${route.params.id}/matches/${matchId}/claim-referee`)
  showToast('认领成功')
  await fetchRounds()
}

const tid = Number(route.params.id)
const { lastMessage } = useWebSocket(tid)
watch(lastMessage, (msg) => { if (msg?.type === 'match_updated') fetchRounds() })
onMounted(() => fetchRounds())
</script>

<style scoped>
.schedule-page { height: 100vh; overflow-y: auto; background: #f0f2f5; padding-bottom: 60px; }
.empty-block { padding-top: 80px; }
.round-section { margin: 0 12px; }
.round-header { display: flex; align-items: center; gap: 8px; padding: 16px 4px 8px; }
.round-label { font-size: 16px; font-weight: 700; color: #333; }
.bye-notice { padding: 8px 12px; margin-bottom: 8px; background: #fff7e6; border-radius: 8px; font-size: 13px; color: #b06d00; }

.match-card {
  background: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,.04); border-left: 4px solid transparent;
}
.match-card.my { border-left-color: #1989fa; background: #f0f7ff; }
.match-card:active { box-shadow: 0 4px 12px rgba(0,0,0,.08); }

.match-teams { display: flex; align-items: center; justify-content: space-between; }
.team { flex: 1; }
.team.win .player-name { color: #07c160; font-weight: 600; }
.team-pair { display: flex; justify-content: center; gap: 8px; }
.player-block { display: flex; flex-direction: column; align-items: center; gap: 3px; }
.player-block.win .player-name { color: #07c160; font-weight: 600; }
.avatar-badge { position: relative; display: inline-block; }
.badge-icon {
  position: absolute; top: -4px; right: -4px;
  font-size: 14px; line-height: 1;
  filter: drop-shadow(0 1px 2px rgba(0,0,0,.3));
}
.player-name { font-size: 12px; color: #333; max-width: 56px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: center; }

.score-box { width: 70px; text-align: center; flex-shrink: 0; }
.score-num { font-size: 20px; font-weight: 800; color: #222; font-variant-numeric: tabular-nums; }
.score-row { font-size: 14px; font-weight: 700; color: #222; }
.score-divider { font-size: 16px; color: #ccc; margin: 0 2px; }


.vs-badge { display: inline-block; padding: 3px 10px; border-radius: 10px; font-size: 12px; font-weight: 700; color: #999; background: #f5f5f5; }

.match-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 11px; color: #aaa; }
.foot-info { color: #999; }
.foot-ref.has { color: #07c160; font-weight: 500; }
</style>
