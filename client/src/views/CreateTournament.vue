<template>
  <div class="page-container">
    <van-nav-bar title="创建赛事" left-text="取消" left-arrow @click-left="$router.back()" />

    <van-form @submit="onSubmit" class="create-form">
      <van-cell-group inset>
        <van-field v-model="form.title" label="赛事名称" placeholder="请输入赛事名称" required :rules="[{ required: true, message: '请输入赛事名称' }]" />
        <van-field v-model="form.description" label="描述" placeholder="赛事描述" type="textarea" />
        <van-field v-model="form.location" label="地点" placeholder="比赛地点" />

        <van-field :model-value="fmtDate(form.start_date)" readonly clickable label="开始时间" placeholder="请选择" required :rules="[{ required: true, message: '请选择开始时间' }]" @click="openPicker('start_date')" />
        <van-field :model-value="fmtDate(form.end_date)" readonly clickable label="结束时间" placeholder="请选择" required :rules="[{ required: true, message: '请选择结束时间' }]" @click="openPicker('end_date')" />

        <van-field v-model.number="form.max_participants" label="最大人数" type="digit" placeholder="8" required :rules="[{ required: true, message: '请输入最大人数' }]" @blur="fetchOptions" />
        <van-field v-model.number="form.entry_fee" label="报名费(元)" type="digit" placeholder="0" />
        <van-field v-model.number="form.points_to_win" label="计分制" type="digit" placeholder="11" />

        <!-- Match count selector -->
        <van-field
          v-if="matchOptions.length > 0"
          :model-value="form.total_matches ? `${form.total_matches} 场 (每人${selectedPerPerson}场)` : '自动'"
          readonly
          clickable
          label="总场次"
          placeholder="自动（最少场次）"
          @click="showMatchPicker = true"
        />
      </van-cell-group>

      <div style="margin: 16px;">
        <van-button round block type="primary" native-type="submit" :loading="submitting">创建赛事</van-button>
      </div>
    </van-form>

    <!-- Date picker popup -->
    <van-popup v-model:show="pickerVisible" position="bottom" round :style="{ height: '55%' }">
      <div class="picker-toolbar">
        <span @click="pickerVisible = false">取消</span>
        <span class="picker-title">选择时间</span>
        <span @click="onPickerConfirm" style="color:#1989fa">确定</span>
      </div>
      <van-tabs v-model:active="pickerTab" color="#1989fa">
        <van-tab title="日期">
          <van-date-picker :show-toolbar="false" v-model="dateValue" :min-date="minDate" :max-date="maxDate" />
        </van-tab>
        <van-tab title="时间">
          <van-time-picker :show-toolbar="false" v-model="timeValue" />
        </van-tab>
      </van-tabs>
    </van-popup>

    <!-- Match count picker -->
    <van-popup v-model:show="showMatchPicker" position="bottom" round :style="{ height: '40%' }">
      <div class="picker-toolbar">
        <span @click="showMatchPicker = false">取消</span>
        <span class="picker-title">选择总场次</span>
      </div>
      <van-cell-group inset style="margin-top:10px">
        <van-cell title="自动（最少场次）" :label="`每人 ${minPerPerson || 0} 场`" @click="selectMatch(null)" :class="{ active: form.total_matches === null }" />
        <van-cell
          v-for="opt in matchOptions"
          :key="opt.total"
          :title="`${opt.total} 场`"
          :label="`每人 ${opt.per_person} 场`"
          @click="selectMatch(opt.total)"
          :class="{ active: form.total_matches === opt.total }"
        />
      </van-cell-group>
    </van-popup>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api/client'
import { showToast } from 'vant'

const router = useRouter()
const submitting = ref(false)

const form = reactive({
  title: '',
  description: '',
  location: '',
  start_date: '',
  end_date: '',
  max_participants: 8,
  entry_fee: 0,
  points_to_win: 11,
  total_matches: null as number | null,
})

const pickerVisible = ref(false)
const pickerTab = ref(0)
const dateValue = ref<string[]>([])
const timeValue = ref<string[]>([])
const minDate = new Date(2026, 0, 1)
const maxDate = new Date(2028, 11, 31)
const currentDateKey = ref('')

const showMatchPicker = ref(false)
const matchOptions = ref<{ total: number; per_person: number }[]>([])

const minPerPerson = computed(() => matchOptions.value[0]?.per_person || 0)
const selectedPerPerson = computed(() => {
  if (!form.total_matches) return minPerPerson.value
  const opt = matchOptions.value.find(o => o.total === form.total_matches)
  return opt?.per_person || 0
})

async function fetchOptions() {
  const n = form.max_participants
  if (n < 4) return
  try {
    const res = await api.get(`/tournaments/match-options/${n}`)
    matchOptions.value = res.data.options || []
    form.total_matches = null
  } catch {}
}

function selectMatch(val: number | null) {
  form.total_matches = val
  showMatchPicker.value = false
}

function fmtDate(d: string) { return d ? d.replace('T', ' ').slice(0, 19) : '' }

function openPicker(key: string) {
  currentDateKey.value = key; pickerTab.value = 0
  const val = (form as any)[key]
  if (val && val.includes('T')) {
    const [dp, tp] = val.split('T')
    dateValue.value = dp.split('-')
    timeValue.value = tp.slice(0, 5).split(':')
  } else {
    const n = new Date()
    dateValue.value = [String(n.getFullYear()), pad(n.getMonth()+1), pad(n.getDate())]
    timeValue.value = [pad(n.getHours()), pad(n.getMinutes())]
  }
  pickerVisible.value = true
}

function pad(n: number) { return String(n).padStart(2, '0') }

function onPickerConfirm() {
  if (currentDateKey.value && dateValue.value.length >= 3) {
    const [y, mo, d] = dateValue.value
    const h = timeValue.value[0] || '00'
    const mi = timeValue.value[1] || '00'
    ;(form as any)[currentDateKey.value] = `${y}-${pad(Number(mo))}-${pad(Number(d))}T${pad(Number(h))}:${pad(Number(mi))}:00`
  }
  pickerVisible.value = false
}

function clean(val: any) { return val === '' || val == null ? null : val }

async function onSubmit() {
  submitting.value = true
  try {
    const payload: Record<string, any> = {
      title: form.title,
      description: clean(form.description),
      location: clean(form.location),
      start_date: form.start_date,
      end_date: form.end_date,
      max_participants: Number(form.max_participants) || 0,
      entry_fee: (Number(form.entry_fee) || 0) * 100,
      total_matches: form.total_matches,
      points_to_win: Number(form.points_to_win) || 11,
      courts: [],
    }
    const res = await api.post('/tournaments', payload)
    showToast('创建成功')
    router.replace(`/tournament/${res.data.id}`)
  } catch {} finally { submitting.value = false }
}

onMounted(() => fetchOptions())
</script>


<style scoped>
.create-form { margin-top: 12px; }
.picker-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; font-size: 15px; }
.picker-title { font-weight: 600; }
.van-cell.active { background: #e8f4ff; }
</style>
