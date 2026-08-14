<template>
  <div class="page-container">
    <van-nav-bar title="创建赛事" left-text="取消" left-arrow @click-left="goBack" />

    <van-form @submit="onSubmit" class="create-form">
      <van-cell-group inset>
        <van-field v-model="form.title" label="赛事名称" placeholder="请输入赛事名称" required :rules="[{ required: true, message: '请输入赛事名称' }]" />
        <van-field v-model="form.location" label="地点" placeholder="比赛地点" />
        <van-field v-model="form.court" label="场地号" placeholder="如 A1、1号场" />

        <!-- 日期 -->
        <van-field :model-value="dateDisplay" readonly clickable label="日期" placeholder="请选择日期" required :rules="[{ required: true, message: '请选择日期' }]" @click="showCalendar = true" />

        <!-- 时间段 -->
        <van-field :model-value="timeDisplay" readonly clickable label="时间段" placeholder="请选择时间段" required :rules="[{ required: true, message: '请选择时间段' }]" @click="showTimePicker = true" />

        <!-- 定时开放报名 -->
        <van-field name="scheduledReg" label="定时开放报名">
          <template #input>
            <van-switch v-model="form.enable_scheduled_registration" size="20" />
          </template>
        </van-field>

        <!-- 报名开放时间 -->
        <van-field
          v-if="form.enable_scheduled_registration"
          :model-value="openTimeDisplay"
          readonly clickable
          label="报名开放时间"
          placeholder="请选择开放报名时间"
          @click="showOpenCalendar = true"
        />

        <van-field name="halfCourt" label="上下场">
          <template #input>
            <van-switch v-model="halfCourt" size="20" />
          </template>
        </van-field>

        <van-field v-model.number="form.max_participants" label="最大人数" type="digit" placeholder="8" required :rules="[{ required: true, message: '请输入最大人数' }]" @blur="fetchOptions" />
        <van-field v-model.number="form.points_to_win" label="计分制" type="digit" placeholder="11" />

        <van-field
          v-if="matchOptions.length > 0"
          :model-value="form.total_matches ? `${form.total_matches} 场 (每人${selectedPerPerson}场)` : '自动'"
          readonly clickable label="总场次" placeholder="自动（推荐14场）"
          @click="showMatchPicker = true"
        />

        <van-field
          v-if="canPreselect"
          :model-value="preselectedIds.length ? `${preselectedIds.length} 人已选` : '未选择'"
          readonly clickable label="默认参赛人员"
          placeholder="可选，创建后自动报名"
          @click="openUserPicker"
        />
      </van-cell-group>

      <div style="margin: 16px;">
        <van-button round block type="primary" native-type="submit" :loading="submitting">创建赛事</van-button>
      </div>
    </van-form>

    <!-- 日历选择 -->
    <van-calendar v-model:show="showCalendar" :min-date="minDate" :default-date="selectedDate" @confirm="onCalendarConfirm" />

    <!-- 报名开放时间：日期 -->
    <van-calendar v-model:show="showOpenCalendar" :min-date="minDate" :default-date="openDate" @confirm="onOpenDateConfirm" />

    <!-- 时间段选择 -->
    <van-popup v-model:show="showTimePicker" position="bottom" round :style="{ height: '55%' }">
      <div class="picker-toolbar">
        <span @click="showTimePicker = false">取消</span>
        <span class="picker-title">选择时间段</span>
        <span @click="onTimeConfirm" style="color:#1989fa">确定</span>
      </div>
      <div class="time-picker-body">
        <div class="time-block">
          <div class="time-label">开始</div>
          <div class="time-pickers">
            <van-picker :columns="hourColumns" v-model="startHourIdx" :show-toolbar="false" />
            <span class="time-colon">:</span>
            <van-picker :columns="minuteColumns" v-model="startMinuteIdx" :show-toolbar="false" />
          </div>
        </div>
        <div class="time-block">
          <div class="time-label">结束</div>
          <div class="time-pickers">
            <van-picker :columns="hourColumns" v-model="endHourIdx" :show-toolbar="false" />
            <span class="time-colon">:</span>
            <van-picker :columns="minuteColumns" v-model="endMinuteIdx" :show-toolbar="false" />
          </div>
        </div>
      </div>
    </van-popup>

    <!-- 报名开放时间：时间 -->
    <van-popup v-model:show="showOpenTimePicker" position="bottom" round :style="{ height: '45%' }">
      <div class="picker-toolbar">
        <span @click="showOpenTimePicker = false">取消</span>
        <span class="picker-title">选择开放时间</span>
        <span @click="onOpenTimeConfirm" style="color:#1989fa">确定</span>
      </div>
      <div class="time-picker-body">
        <div class="time-block">
          <div class="time-pickers">
            <van-picker :columns="hourColumns" v-model="openHourIdx" :show-toolbar="false" />
            <span class="time-colon">:</span>
            <van-picker :columns="minuteColumns" v-model="openMinuteIdx" :show-toolbar="false" />
          </div>
        </div>
      </div>
    </van-popup>

    <!-- 场次选择 -->
    <van-popup v-model:show="showMatchPicker" position="bottom" round :style="{ height: '40%' }">
      <div class="picker-toolbar">
        <span @click="showMatchPicker = false">取消</span>
        <span class="picker-title">选择总场次</span>
      </div>
      <van-cell-group inset style="margin-top:10px">
        <van-cell title="自动" :label="closestMatch ? `${closestMatch.total} 场 · 每人 ${closestMatch.per_person} 场` : ''" @click="selectMatch(null)" :class="{ active: form.total_matches === closestMatch?.total }" />
        <van-cell
          v-for="opt in matchOptions" :key="opt.total"
          :title="`${opt.total} 场`" :label="`每人 ${opt.per_person} 场`"
          @click="selectMatch(opt.total)" :class="{ active: form.total_matches === opt.total }"
        />
      </van-cell-group>
    </van-popup>

    <!-- 默认参赛人员选择 -->
    <van-popup v-model:show="showUserPicker" position="bottom" round :style="{ height: '60%' }">
      <div class="picker-toolbar">
        <span @click="showUserPicker = false">取消</span>
        <span class="picker-title">选择默认参赛人员</span>
        <span @click="showUserPicker = false" style="color:#1989fa">完成</span>
      </div>
      <div class="user-picker-body">
        <van-cell
          v-for="u in selectableUsers"
          :key="u.id"
          clickable
          :title="u.username"
          :label="userRoleLabel(u.role)"
          @click="toggleUser(u.id)"
        >
          <template #right-icon>
            <van-checkbox :model-value="preselectedIds.includes(u.id)" @click.stop="toggleUser(u.id)" />
          </template>
        </van-cell>
        <van-empty v-if="selectableUsers.length === 0" description="暂无用户" />
      </div>
    </van-popup>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import api from '@/api/client'
import { useGoBack } from '@/composables/useGoBack'
import { showToast } from 'vant'

const router = useRouter()
const auth = useAuthStore()
const { goBack } = useGoBack()
const submitting = ref(false)
const halfCourt = ref(false)

const form = reactive({
  title: '',
  location: '',
  court: '',
  start_date: '',
  end_date: '',
  max_participants: 8,
  points_to_win: 11,
  total_matches: null as number | null,
  enable_scheduled_registration: false,
  registration_open_at: '',
})

// --- 日期 ---
function getDefaultDate(): Date {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const d = today.getDay()
  if (d === 5) return today
  if (d === 6 || d === 0) return today
  const friday = new Date(today)
  friday.setDate(today.getDate() + (5 - d))
  return friday
}

const showCalendar = ref(false)
const selectedDate = ref(getDefaultDate())
const minDate = new Date(); minDate.setHours(0, 0, 0, 0)

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const dateDisplay = computed(() => {
  const d = selectedDate.value
  const m = d.getMonth() + 1
  const day = d.getDate()
  return `${m}月${day}日 ${WEEKDAYS[d.getDay()]}`
})

async function onCalendarConfirm(date: Date) {
  selectedDate.value = date
  showCalendar.value = false
  syncDateTime()
  await fetchDefaultTitle()
}

// --- 时间段 ---
const showTimePicker = ref(false)
const hourColumns = Array.from({ length: 24 }, (_, i) => ({ text: String(i).padStart(2, '0'), value: i }))
const minuteColumns = Array.from({ length: 12 }, (_, i) => ({ text: String(i * 5).padStart(2, '0'), value: i * 5 }))

// Vant Picker 的 v-model 绑定的是选中项的值（不是索引），
// 这里直接以值为准；分钟列值为 0/5/10...，绝不能当数组下标用
const startHourIdx = ref([19])
const startMinuteIdx = ref([0])
const endHourIdx = ref([21])
const endMinuteIdx = ref([0])

const startHour = computed(() => Number(startHourIdx.value[0]) || 0)
const startMinute = computed(() => Number(startMinuteIdx.value[0]) || 0)
const endHour = computed(() => Number(endHourIdx.value[0]) || 0)
const endMinute = computed(() => Number(endMinuteIdx.value[0]) || 0)

const timeDisplay = computed(() => {
  const s = `${String(startHour.value).padStart(2, '0')}:${String(startMinute.value).padStart(2, '0')}`
  const e = `${String(endHour.value).padStart(2, '0')}:${String(endMinute.value).padStart(2, '0')}`
  return `${s} ~ ${e}`
})

function onTimeConfirm() {
  const sh = startHour.value; const sm = startMinute.value
  const eh = endHour.value; const em = endMinute.value
  if (sh > eh || (sh === eh && sm >= em)) {
    showToast('结束时间必须晚于开始时间')
    return
  }
  showTimePicker.value = false
  syncDateTime()
}

function syncDateTime() {
  const d = selectedDate.value
  const iso = (h: number, m: number) => {
    const y = d.getFullYear()
    const mo = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${mo}-${day}T${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`
  }
  form.start_date = iso(startHour.value, startMinute.value)
  form.end_date = iso(endHour.value, endMinute.value)
}

// --- 定时开放报名 ---
const showOpenCalendar = ref(false)
const showOpenTimePicker = ref(false)
function nextFullHour(): [number, number] {
  const d = new Date()
  d.setMinutes(0, 0, 0)
  d.setHours(d.getHours() + 1)
  return [d.getHours(), d.getMinutes()]
}
const [openDefaultH, openDefaultM] = nextFullHour()
const openDate = ref<Date>(new Date())
const openHourIdx = ref([openDefaultH])
const openMinuteIdx = ref([openDefaultM])
const openHour = computed(() => Number(openHourIdx.value[0]) || 0)
const openMinute = computed(() => Number(openMinuteIdx.value[0]) || 0)
const openTimeDisplay = computed(() => {
  const d = openDate.value
  const m = d.getMonth() + 1
  const day = d.getDate()
  return `${m}月${day}日 ${WEEKDAYS[d.getDay()]} ${String(openHour.value).padStart(2, '0')}:${String(openMinute.value).padStart(2, '0')}`
})
function syncOpenTime() {
  const d = openDate.value
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  form.registration_open_at = `${y}-${mo}-${day}T${String(openHour.value).padStart(2, '0')}:${String(openMinute.value).padStart(2, '0')}:00`
}
function onOpenDateConfirm(date: Date) {
  openDate.value = date
  showOpenCalendar.value = false
  showOpenTimePicker.value = true
}
function onOpenTimeConfirm() {
  showOpenTimePicker.value = false
  syncOpenTime()
}

const defaultTitle = ref('')

async function fetchDefaultTitle() {
  const d = selectedDate.value
  const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  try {
    const res = await api.get('/tournaments/default-title', { params: { date: iso } })
    const title = res.data.title
    if (!form.title || form.title === defaultTitle.value) form.title = title
    defaultTitle.value = title
  } catch {}
}

// 初始化
syncDateTime()
syncOpenTime()

// --- 场次 ---
const showMatchPicker = ref(false)
const matchOptions = ref<{ total: number; per_person: number }[]>([])
const showUserPicker = ref(false)
const selectableUsers = ref<any[]>([])
const preselectedIds = ref<number[]>([])
let selectableUsersLoaded = false

const canPreselect = computed(() =>
  !!auth.user && (auth.user.role === 'admin' || auth.user.role === 'superadmin')
)

function userRoleLabel(role: string) {
  return role === 'superadmin' ? '超级管理员' : role === 'admin' ? '管理员' : '普通用户'
}

function toggleUser(id: number) {
  const idx = preselectedIds.value.indexOf(id)
  if (idx >= 0) preselectedIds.value.splice(idx, 1)
  else preselectedIds.value.push(id)
}

async function fetchSelectableUsers() {
  if (!canPreselect.value) return false
  if (selectableUsersLoaded) return true
  try {
    const res = await api.get('/auth/admin/selectable-users', { skipLoading: true } as any)
    selectableUsers.value = res.data
    selectableUsersLoaded = true
    return true
  } catch {
    return false
  }
}

async function openUserPicker() {
  if (await fetchSelectableUsers()) {
    showUserPicker.value = true
  }
}

const closestMatch = computed(() => {
  if (matchOptions.value.length === 0) return null
  let best = matchOptions.value[0]
  for (const opt of matchOptions.value) {
    if (Math.abs(opt.total - 14) < Math.abs(best.total - 14)) best = opt
  }
  return best
})
const minPerPerson = computed(() => matchOptions.value[0]?.per_person || 0)
const selectedPerPerson = computed(() => {
  const total = form.total_matches ?? closestMatch.value?.total
  if (!total) return 0
  const opt = matchOptions.value.find(o => o.total === total)
  return opt?.per_person || 0
})

async function fetchOptions() {
  const n = form.max_participants
  if (n < 4) return
  try {
    const res = await api.get(`/tournaments/match-options/${n}`)
    matchOptions.value = res.data.options || []
    if (matchOptions.value.length > 0) {
      const best = matchOptions.value.reduce((a, b) =>
        Math.abs(a.total - 14) < Math.abs(b.total - 14) ? a : b
      )
      form.total_matches = best.total
    } else {
      form.total_matches = null
    }
  } catch {}
}

function selectMatch(val: number | null) {
  form.total_matches = val ?? closestMatch.value?.total ?? null
  showMatchPicker.value = false
}

function clean(val: any) { return val === '' || val == null ? null : val }

function buildPayload(title: string, start: string, end: string) {
  return {
    title,
    location: clean(form.location),
    start_date: start,
    end_date: end,
    max_participants: Number(form.max_participants) || 0,
    total_matches: form.total_matches,
    points_to_win: Number(form.points_to_win) || 11,
    enable_scheduled_registration: form.enable_scheduled_registration,
    registration_open_at: form.enable_scheduled_registration ? form.registration_open_at : null,
    preselect_player_ids: preselectedIds.value,
    courts: form.court ? [{
      name: form.court,
      sort_order: 0,
      time_slots: [{ start_time: start.slice(11), end_time: end.slice(11) }],
    }] : [],
  }
}

async function onSubmit() {
  submitting.value = true
  try {
    if (halfCourt.value) {
      const sh = startHour.value * 60 + startMinute.value
      const eh = endHour.value * 60 + endMinute.value
      const mid = sh + Math.floor((eh - sh) / 2)
      const mh = Math.floor(mid / 60)
      const mm = mid % 60
      const midTime = `${String(mh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00`
      const d = selectedDate.value
      const y = d.getFullYear()
      const mo = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      const dp = `${y}-${mo}-${day}T`
      const st = `${String(startHour.value).padStart(2, '0')}:${String(startMinute.value).padStart(2, '0')}:00`
      const et = `${String(endHour.value).padStart(2, '0')}:${String(endMinute.value).padStart(2, '0')}:00`
      const upper = buildPayload(form.title + '（上半场）', dp + st, dp + midTime)
      const lower = buildPayload(form.title + '（下半场）', dp + midTime, dp + et)
      await api.post('/tournaments/batch', [upper, lower])
      showToast('上下场赛事已创建')
    } else {
      await api.post('/tournaments', buildPayload(form.title, form.start_date, form.end_date))
      showToast('创建成功')
    }
    router.replace('/')
  } catch {} finally { submitting.value = false }
}

onMounted(async () => {
  await Promise.all([auth.fetchMe(), fetchOptions(), fetchDefaultTitle()])
})
</script>

<style scoped>
.create-form { margin-top: 12px; }
.picker-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; font-size: 15px; }
.picker-title { font-weight: 600; }
.van-cell.active { background: #e8f4ff; }

.time-picker-body { display: flex; padding: 16px 0; }
.time-block { flex: 1; }
.time-label { text-align: center; font-size: 14px; color: #666; margin-bottom: 8px; }
.time-pickers { display: flex; align-items: center; justify-content: center; }
.time-pickers .van-picker { flex: 1; }
.time-colon { font-size: 20px; font-weight: 700; color: #333; margin: 0 4px; }
.user-picker-body { max-height: calc(60vh - 44px); overflow-y: auto; }
</style>
