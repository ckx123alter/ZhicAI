<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart, LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, MarkLineComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
echarts.use([PieChart,LineChart,TooltipComponent,GridComponent,MarkLineComponent,SVGRenderer])
const props=defineProps<{option:any;label:string}>(); const el=ref<HTMLElement>();let chart:echarts.ECharts;let observer:ResizeObserver
onMounted(()=>{chart=echarts.init(el.value,undefined,{renderer:'svg'});chart.setOption(props.option);observer=new ResizeObserver(()=>chart.resize());observer.observe(el.value!)} )
watch(()=>props.option,v=>chart?.setOption(v,true),{deep:true});onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose()})
</script>
<template><div ref="el" class="chart" role="img" :aria-label="label"></div></template>
