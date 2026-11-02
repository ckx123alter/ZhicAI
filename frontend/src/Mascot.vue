<script setup lang="ts">
withDefaults(defineProps<{
  state?: 'idle' | 'listening' | 'thinking' | 'success' | 'travel' | 'greeting' | 'warning' | 'landing' | 'surprised' | 'riskReject'
  compact?: boolean
}>(), { state: 'idle', compact: false })
</script>

<template>
  <span class="mascot-character" :class="['mascot--'+state, { 'mascot--compact': compact }]" role="img" aria-label="知财：深蓝叶子的简笔伙伴">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 168" fill="none" aria-hidden="true">
      <g class="character-body" id="body">
        <g class="leaf leaf-left" id="left-leaf">
          <path d="M70 62C49 59 29 39 30 20C52 15 74 33 70 62Z" fill="#244E7A" stroke="#173953" stroke-width="2.8" stroke-linejoin="round"/>
          <path d="M67 55C59 44 49 34 40 28" stroke="#C1D2E4" stroke-width="1.8" stroke-linecap="round"/>
        </g>
        <g class="leaf leaf-right" id="right-leaf">
          <path d="M84 61C82 36 102 11 129 11C130 33 108 56 84 61Z" fill="#244E7A" stroke="#173953" stroke-width="2.8" stroke-linejoin="round"/>
          <path d="M90 54C98 40 108 29 119 21" stroke="#C1D2E4" stroke-width="1.8" stroke-linecap="round"/>
        </g>
        <path id="body-outline" d="M59.96 62.76L63.04 59.24C64.59 57.47 65.24 57.15 67.24 58.4L70.76 60.6C72.87 61.92 73.37 61.68 75.24 60.04L78.76 56.96C80.62 55.33 81.49 55.49 83.24 57.24L86.76 60.76C88.59 62.59 88.82 63.07 91.24 62.16L94.76 60.84C96.58 60.16 97.15 60.27 98.12 61.96L99.88 65.04C101.3 67.53 101.63 66.96 104.43 67.58C118.6 70.68 129.38 82.21 131.63 95.75C132.12 98.71 131.68 98.73 133.96 100.68L137.04 103.32C138.21 104.32 138.38 105.36 137.04 106.12L133.96 107.88C131.57 109.24 132.18 109.83 132.26 112.58C132.67 126.9 125.34 139.44 114.7 146.39C112.57 147.79 112.25 147.26 111.44 149.68L110.56 152.32C110.05 153.85 109.27 153.72 107.76 153.16L104.24 151.84C101.5 150.81 101.66 151.24 98.94 152.31C85.52 157.57 69.55 157.62 56.87 153.12C54.22 152.18 54.4 151.85 51.76 152.84L48.24 154.16C46.55 154.79 45.87 154.7 45.16 153.04L43.84 149.96C42.67 147.23 42.39 147.84 39.83 146.34C28.17 139.52 22.99 127.87 23.69 114.5C23.83 111.71 24.47 111.39 22.32 109.6L19.68 107.4C18.24 106.2 18.83 105.27 20.24 104.04L23.76 100.96C26.3 98.74 25.88 98.73 26.34 95.39C28.54 79.65 39.49 69.32 54.56 65.7C57.85 64.91 57.73 65.3 59.96 62.76Z" fill="#FFFFFF" stroke="#173953" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>
        <g class="face" id="face">
          <g v-if="state==='riskReject'" class="eyes risk-reject-eyes" id="eyes" fill="#173953" stroke="#173953" stroke-width="2.6" stroke-linecap="round">
            <path d="M52 98L65 111M65 98L52 111M94 98L107 111M107 98L94 111" fill="none"/>
          </g>
          <g v-else class="eyes" id="eyes" fill="#173953">
            <ellipse cx="59" cy="105" rx="4.3" ry="7"/>
            <ellipse cx="99" cy="105" rx="4.3" ry="7"/>
          </g>
          <path class="smile" id="mouth" d="M73 121Q79 127 86 121" stroke="#173953" stroke-width="2.5" stroke-linecap="round"/>
          <circle class="thinking-mouth" cx="80" cy="122" r="3" stroke="#173953" stroke-width="2.2"/>
        </g>
      </g>
    </svg>
  </span>
</template>

<style scoped>
.mascot-character{display:block;width:100%;height:100%;color:#173953;pointer-events:none;padding:0;margin:0;border:0;box-sizing:border-box;background:transparent;flex-shrink:0}
svg{display:block;width:100%;height:100%;overflow:visible}
.character-body{transform-origin:80px 150px;animation:float 4.8s ease-in-out infinite}
.leaf-left{transform-origin:69px 60px;animation:leaf-left 5.2s ease-in-out infinite}
.leaf-right{transform-origin:85px 59px;animation:leaf-right 5.2s ease-in-out infinite}
.eyes{transform-origin:79px 105px;animation:blink 6s linear infinite}
.face{transform-origin:80px 112px;transition:transform .3s ease}
.thinking-mouth{opacity:0;transition:opacity .2s ease}
.mascot--listening .face{transform:translateY(-3px)}
.mascot--listening .leaf-right{animation:listen-leaf 1.8s ease-in-out infinite}
.mascot--thinking .character-body{animation:ponder 2s ease-in-out infinite}
.mascot--thinking .leaf-left,.mascot--thinking .leaf-right{animation-duration:1.2s}
.mascot--thinking .thinking-mouth{opacity:1}
.mascot--thinking .smile{opacity:0}
.mascot--success .character-body{animation:nod .7s ease-in-out 2}
.mascot--success .leaf-right{animation:listen-leaf .7s ease-in-out 2}
.mascot--landing .character-body{animation:happy-hop .52s ease-in-out 2}
.mascot--landing .leaf-left{animation:landing-leaf-left .52s ease-in-out 2}
.mascot--landing .leaf-right{animation:landing-leaf-right .52s ease-in-out 2}
.mascot--surprised .character-body{animation:surprise-recoil .7s ease-out both}
.mascot--surprised .leaf-left{animation:surprise-leaf-left .7s ease-out both}
.mascot--surprised .leaf-right{animation:surprise-leaf-right .7s ease-out both}
.mascot--surprised .eyes{animation:surprise-eyes .7s ease-out both}
.mascot--surprised .smile{opacity:0}
.mascot--surprised .thinking-mouth{opacity:1;transform-origin:80px 122px;transform:scale(1.7)}
.mascot--riskReject .character-body{animation:risk-reject-hop .9s cubic-bezier(.2,.8,.2,1) both}
.mascot--riskReject .leaf-left{animation:surprise-leaf-left .9s ease-out both}
.mascot--riskReject .leaf-right{animation:surprise-leaf-right .9s ease-out both}
.mascot--riskReject .smile{opacity:0}
.mascot--riskReject .thinking-mouth{opacity:1;transform-origin:80px 122px;transform:scale(1.4)}
.mascot--greeting .character-body{animation:greet .75s ease both}
.mascot--greeting .leaf-left{animation:wave .75s ease both}
.mascot--travel .character-body{animation:travel .65s ease both}
.mascot--travel .leaf-left,.mascot--travel .leaf-right{animation:wave .65s ease both}
.mascot--compact .character-body,.mascot--compact .leaf,.mascot--compact .eyes{animation:none}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@keyframes leaf-left{0%,100%{transform:rotate(0)}50%{transform:rotate(-4deg)}}
@keyframes leaf-right{0%,100%{transform:rotate(0)}50%{transform:rotate(5deg)}}
@keyframes blink{0%,43%,47%,100%{transform:scaleY(1)}45%{transform:scaleY(.08)}}
@keyframes listen-leaf{0%,100%{transform:rotate(0)}50%{transform:rotate(-10deg)}}
@keyframes ponder{0%,100%{transform:rotate(-3deg)}50%{transform:rotate(3deg) translateY(-2px)}}
@keyframes nod{0%,100%{transform:translateY(0) scaleY(1)}40%{transform:translateY(3px) scaleY(.96)}70%{transform:translateY(-3px) scaleY(1.02)}}
@keyframes happy-hop{0%,100%{transform:translateY(0) scale(1)}14%{transform:translateY(2px) scale(1.05,.93)}55%{transform:translateY(-10px) scale(.98,1.04)}76%{transform:translateY(-5px) scale(1)}90%{transform:translateY(0) scale(1.02,.96)}}
@keyframes landing-leaf-left{0%,100%{transform:rotate(0)}55%{transform:rotate(-11deg)}}
@keyframes landing-leaf-right{0%,100%{transform:rotate(0)}55%{transform:rotate(11deg)}}
@keyframes surprise-recoil{0%,100%{transform:translateY(0) rotate(0) scale(1)}18%{transform:translateY(4px) rotate(-5deg) scale(.9,.94)}42%{transform:translateY(-7px) rotate(5deg) scale(1.08,1.06)}68%{transform:translateY(-2px) rotate(-2deg) scale(.98,1.02)}}
@keyframes surprise-leaf-left{0%,100%{transform:rotate(0)}32%{transform:rotate(-22deg)}65%{transform:rotate(7deg)}}
@keyframes surprise-leaf-right{0%,100%{transform:rotate(0)}32%{transform:rotate(22deg)}65%{transform:rotate(-7deg)}}
@keyframes surprise-eyes{0%,100%{transform:scaleY(1)}24%,55%{transform:scaleY(1.3)}}
@keyframes risk-reject-hop{0%,100%{transform:translateY(0) scale(1)}18%{transform:translateY(5px) scale(.9,.92)}45%{transform:translateY(-24px) scale(1.08,1.06)}68%{transform:translateY(-12px) scale(.99,1.02)}86%{transform:translateY(1px) scale(1.02,.97)}}
@keyframes greet{0%,100%{transform:scale(1)}30%{transform:scale(1.06,.94)}60%{transform:translateY(-7px) scale(.97,1.03)}}
@keyframes wave{0%,100%{transform:rotate(0)}25%{transform:rotate(-12deg)}65%{transform:rotate(8deg)}}
@keyframes travel{0%{transform:rotate(-8deg) scale(.95,1.04)}60%{transform:rotate(7deg)}100%{transform:rotate(0) scale(1)}}
@media(prefers-reduced-motion:reduce){.character-body,.leaf,.eyes{animation:none!important}.face,.thinking-mouth{transition:none}}
</style>


