const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const dom=new JSDOM('<main id="root"></main>',{runScripts:'outside-only',pretendToBeVisual:true,url:'http://localhost'});
const w=dom.window;
w.eval(fs.readFileSync(path.join(process.env.ODOO_PROJECT_DIR || path.join(root,'../odoo-19.0'),'addons/web/static/lib/owl/owl.js'),'utf8'));
Object.assign(w, { Component:w.owl.Component, useState:w.owl.useState, useRef:w.owl.useRef, useEffect:w.owl.useEffect, onWillUnmount:w.owl.onWillUnmount });
w.eval(fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/bms/bms.js','utf8').replace(/^import .*;$/mg,'').replace('export class BmsPanel','window.BmsPanel = class BmsPanel'));
const b={id:1,name:'Pin test',timestamp:new Date().toISOString(),stale_seconds:10,offline_seconds:60,
 cells:Array(16).fill(3.274),soc:34,voltage:52.376,current:-3.8,power:-199.0288,
 bms_model:'JK_PB1A16S10P',firmware:'19.10',flow_status:'Đang xả',cell_min_index:5,cell_max_index:2,
 cell_min_voltage:3.272,cell_max_voltage:3.276,cell_delta_voltage:.004,cell_delta_warning:.03,
 battery_temperature_1:34.7,battery_temperature_2:34.3,mos_temperature:34.2};
(async()=>{
 const app=new w.owl.App(w.BmsPanel,{props:{batteries:[b],history:[],theme:'light'},templates:fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/bms/bms.xml','utf8')});
 const card=await app.mount(w.document.getElementById('root'));
 assert.equal(w.document.querySelectorAll('.ss_bms_cells > div').length,16);
 assert(w.document.body.textContent.includes('52.376 V'));
 assert(w.document.body.textContent.includes('-3.800 A'));
 assert(w.document.body.textContent.includes('ONLINE'));
 assert.equal(w.document.querySelector('progress').value,34);
 assert.equal(w.document.querySelectorAll('.ss_bms_cells .min').length,1);
 assert.equal(w.document.querySelectorAll('.ss_bms_cells .max').length,1);

 card.state.now=Date.now()+11000; await new Promise(r=>w.requestAnimationFrame(()=>w.requestAnimationFrame(r)));
 assert(w.document.body.textContent.includes('STALE'));
 card.state.now=Date.now()+61000; await new Promise(r=>w.requestAnimationFrame(()=>w.requestAnimationFrame(r)));
 assert(w.document.body.textContent.includes('OFFLINE'));
 app.destroy();dom.window.close();console.log('PASS real OWL mount: 16 cells, signed values, SOC, min/max, ONLINE/STALE/OFFLINE');
})().catch(e=>{console.error(e);dom.window.close();process.exit(1)});
