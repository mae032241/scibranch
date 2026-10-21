"""Small escaped scientific result presentation, with no state or service imports."""
import base64
import hashlib
import html
import json

from .configuration_form import SCRIPT_CSP as CONFIG_SCRIPT_CSP
from .ui_guidance import SCRIPT_CSP as GUIDANCE_SCRIPT_CSP
from . import ui_components as ui, ui_translation_client as translation_client


CSS = '''
:root{color-scheme:light;font:16px/1.65 system-ui,-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;color:#263c43;background:#f5f7f8;font-synthesis:none}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0}main{max-width:1120px;margin:auto;padding:28px 36px 72px}
.app-brand{display:flex;align-items:center;gap:10px;padding-bottom:25px;margin-bottom:26px;border-bottom:1px solid #dce4e6;color:#244c4d;font-size:.85rem;font-weight:750;letter-spacing:.04em}.brand-mark{width:26px;height:26px;display:inline-grid;place-items:center;border-radius:8px;background:#176b5b;color:#fff;font-size:.9rem}.brand-caption{margin-left:auto;color:#718086;font-weight:450;letter-spacing:0}
h1{margin:0 0 15px;font-size:2rem;line-height:1.35;letter-spacing:-.035em;color:#203b40}h2{font-size:1.35rem;line-height:1.45;margin:0 0 12px}h3{font-size:1rem;margin:24px 0 8px}p{margin:10px 0 16px}nav{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:24px}a{color:#236653;text-underline-offset:4px}nav a{font-size:.9rem;font-weight:600}
section,details{background:white;border:1px solid #dce4e6;border-radius:14px;padding:24px;margin:20px 0;overflow-wrap:anywhere}summary{cursor:pointer;font-weight:650;line-height:1.65}summary::marker{color:#7b8e91}details[open]>summary{margin-bottom:16px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}.cards section{margin:0}.cards p{margin:8px 0 20px}.metric{font:600 1.35rem/1.4 ui-monospace,monospace;overflow-wrap:anywhere}.note{color:#66797e;font-size:.9rem}.gap{color:#814021}.finding{white-space:pre-wrap;overflow-wrap:anywhere}.badge{display:inline-block;padding:3px 10px;background:#e6f1ed;border-radius:20px}.hero{border-left:5px solid #176b5b}.empty{text-align:center;padding:28px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.82rem}code{overflow-wrap:anywhere;font-size:.85em}li{margin:8px 0}table{border-collapse:collapse;width:100%}td,th{text-align:left;border-bottom:1px solid #e4e9eb;padding:12px;vertical-align:top}th{font-weight:600}
button,.primary{display:inline-block;padding:12px 22px;border:1px solid transparent;border-radius:9px;background:#176b5b;color:white;font:inherit;font-size:.95rem;font-weight:650;cursor:pointer;text-decoration:none;line-height:1.5;transition:background .15s}button:hover,.primary:hover{background:#105346}button:disabled{opacity:.5;cursor:default}.secondary-button,.secondary button{background:#fff;border:1px solid #cbd8da;color:#38575d}.secondary-button:hover,.secondary button:hover{background:#eef4f3}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #8cbab0;outline-offset:4px}form{margin:16px 0}label{display:block;margin:16px 0 8px;font-size:.94rem;font-weight:650}input,select,textarea{font:inherit;font-size:.95rem;padding:11px 13px;border:1px solid #b9c9ce;border-radius:8px;background:#fff;color:#203b40;max-width:100%}input:not([type=checkbox]):not([type=hidden]),select,textarea{display:block;width:100%}input::placeholder,textarea::placeholder{color:#8a999e}input:focus,select:focus,textarea:focus{outline:3px solid #dfefea;border-color:#4b907c}textarea{min-height:100px;resize:vertical}input[type=checkbox]{accent-color:#176b5b;vertical-align:middle;margin-right:8px}.check-label{font-weight:450}.event{padding:12px 0;border-bottom:1px solid #e2e7e8}.event:last-child{border:0}.event p{white-space:pre-wrap;margin:5px 0}.steps{display:flex;gap:10px;flex-wrap:wrap;padding:0;list-style:none}.steps li{padding:8px 12px;border-radius:6px;background:#e0ede8}
.connection{font-size:.8rem;color:#5d7875;margin:0 0 18px}.connection[data-state=offline]{color:#946027}.setup-intro{color:#62777e;max-width:800px;margin-bottom:26px}.setup-nav{margin:0 0 18px}.setup-flow{max-width:940px}.setup-progress{display:grid;grid-template-columns:repeat(3,1fr);list-style:none;margin:28px 0 25px;padding:0;border-bottom:1px solid #d8e3e5}.setup-progress li{position:relative;display:flex;gap:10px;align-items:center;padding:0 0 20px;margin:0;color:#7d8e93;font-size:.95rem;font-weight:550}.setup-progress li span{display:grid;place-items:center;width:29px;height:29px;background:#e9edef;border-radius:50%;font-size:.85rem}.setup-progress li.is-current{color:#176b5b;font-weight:750}.setup-progress li.is-current:after{position:absolute;bottom:-1px;left:0;width:90%;height:3px;background:#176b5b;content:"";border-radius:3px}.setup-progress li.is-current span{background:#176b5b;color:white}.setup-progress li.is-complete span{background:#dcece6;color:#176b5b}
.setup-panel{padding:32px 38px;margin:0;box-shadow:0 8px 30px #223a4010}.section-heading{max-width:760px;margin-bottom:24px}.eyebrow{display:block;font-size:.75rem;letter-spacing:.08em;color:#64877d;font-weight:750;margin-bottom:9px}.section-heading h2{font-size:1.55rem;letter-spacing:-.025em;margin-bottom:10px}.section-heading p{font-size:.92rem;color:#62777e;margin:0}.section-heading h2:focus{outline:none}.config-field{min-width:0;margin:24px 0}.config-field>label{margin:0 0 9px}.required-mark{display:inline-block;color:#6c827c;background:#edf4f1;border-radius:4px;padding:1px 6px;font-size:.67rem;line-height:1.5;margin-left:6px;font-weight:500;vertical-align:middle}.field-help{display:block;color:#687e83;font-size:.81rem;font-weight:400;margin-top:9px;line-height:1.7;max-width:750px}.field-error{margin:8px 0;color:#a2542c;font-size:.82rem}.field-key{display:block;font:400 .72rem/1.6 ui-monospace,monospace;color:#84969b;overflow-wrap:anywhere;margin-top:4px}.task-mode{max-width:320px;margin-bottom:22px}.config-field textarea[name=task_text]{min-height:170px}.path-heading{border-top:1px solid #e7ecee;padding-top:3px;margin-top:30px}.path-heading h3{font-size:1.05rem;margin-bottom:4px}.path-heading .note{margin-top:0}.path-base{color:#7b8c91;font-size:.78rem;margin:23px 0 0;padding:0;border:0;background:transparent}.path-base>summary{font-weight:450}.path-base p{font-size:.76rem;margin:10px 0 0}.path-base code{display:block;margin-top:6px}.setup-actions{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;margin-top:30px;padding-top:23px;border-top:1px solid #e5ebec}.setup-actions>.note{font-size:.8rem;margin:0}.setup-actions form{margin:0}.setup-check{display:block;margin:22px 0 0 auto}.model-fields{display:grid;grid-template-columns:2fr 1fr 1fr;gap:20px}.model-fields .config-field,.budget-fields .config-field{margin:4px 0 20px}.subsection-title{margin:16px 0 18px;padding-top:22px;border-top:1px solid #e5ebec}.budget-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.summary-note{display:inline-block;margin-left:14px;color:#809297;font-size:.78rem;font-weight:400}.permission-settings,.advanced-settings{background:#fafcfc;border-radius:10px;padding:19px 22px;margin:20px 0 0}.permission-settings .config-field{margin:18px 0}.advanced-category,.structured-setting{border-radius:8px;padding:16px 20px;margin:12px 0;background:white}.advanced-category>summary{font-size:.9rem}.advanced-category .note{font-size:.8rem}.config-node{padding:15px;border:1px solid #dce5e8;border-radius:8px;margin:12px 0;background:#f9fbfb}.config-node label{font-size:.8rem;margin:10px 0}.config-node button,.config-string-row button,.advanced-category button{font-size:.78rem;padding:7px 12px}.config-node .config-children{margin-left:14px}.config-string-row{display:flex;align-items:start;gap:10px;margin:10px 0}.config-string-row textarea{min-height:46px}.config-string-row button{flex-shrink:0}.setup-tools{padding:18px 24px;margin:24px 0 0;background:transparent;border-style:dashed}.setup-tools>summary{font-size:.88rem;color:#607b7d}.tools-grid{display:grid;grid-template-columns:1fr 1fr;gap:28px}.tools-grid>div{min-width:0}.tools-grid h3{margin:8px 0}.tools-grid button{font-size:.8rem;padding:9px 14px}.tools-grid label{font-size:.81rem}.tools-grid input{font-size:.82rem}.setup-notice,#config-edited-note{padding:17px 22px;border:1px solid #dfba83;background:#fff8ed;color:#865718;border-radius:10px;font-size:.9rem}.import-notice{padding:13px 18px;border:1px solid #c7dfd5;background:#edf6f0;color:#466b5a;border-radius:9px;font-size:.82rem}.review-summary{margin:26px 0;display:grid;grid-template-columns:1fr 1fr;gap:0 28px}.review-summary>div{padding:15px 0;border-bottom:1px solid #e5ebed;min-width:0}.review-summary dt{font-size:.8rem;color:#6c8388;margin-bottom:5px}.review-summary dd{margin:0;font-size:.95rem;font-weight:600;overflow-wrap:anywhere}.review-path{font-size:.9rem;font-weight:550}.review-full-path{padding:0;margin:7px 0 0;border:0;background:transparent;border-radius:0;color:#71868c;font-size:.73rem;font-weight:400}.review-full-path>summary{font-weight:450}.review-full-path[open]>summary{margin-bottom:7px}.review-full-path code{display:block;font-size:.72rem;font-weight:400;color:#6e8187;line-height:1.65}.confirmation>.finding{padding:19px 22px;background:#f5f8f7;border-radius:9px;font-size:.94rem}.permission-review{font-size:.82rem}.permission-review th:first-child{width:26%}.permission-limit{font-size:.8rem;color:#855a32}.effective-details{padding:15px 19px;font-size:.83rem}.advanced-review{font-size:.84rem;color:#60777d;margin-top:24px}.confirmation>.setup-actions{margin-top:24px}
@media(max-width:750px){main{padding:22px 20px 50px}.brand-caption{display:none}.setup-panel{padding:25px}.model-fields{grid-template-columns:1fr 1fr}.model-fields .config-field:first-child{grid-column:1/-1}.budget-fields{grid-template-columns:1fr}.budget-fields .config-field{margin-bottom:13px}.budget-fields .field-help{max-width:none}.tools-grid{grid-template-columns:1fr}.setup-progress{gap:10px}.setup-progress li{font-size:.8rem;gap:6px}.setup-progress li span{width:25px;height:25px;flex-shrink:0}.summary-note{display:block;margin:5px 0 0 20px}.review-summary{grid-template-columns:1fr}.setup-actions{align-items:stretch}.setup-actions>button,.setup-actions form,.setup-actions form button{width:100%}.setup-actions>.note{order:2}.setup-check{width:100%}.permission-review td,.permission-review th{padding:9px 6px}.config-node .config-children{margin-left:0}}
@media(max-width:450px){main{padding:18px 14px 40px}.app-brand{padding-bottom:18px;margin-bottom:22px}h1{font-size:1.65rem}section,details{padding:18px}.setup-panel{padding:22px 18px}.section-heading h2{font-size:1.3rem}.setup-progress li{font-size:.73rem;gap:5px;align-items:flex-start}.setup-progress li span{width:21px;height:21px;font-size:.7rem}.model-fields{display:block}.model-fields .config-field{margin:22px 0}.permission-settings,.advanced-settings{padding:16px}.advanced-category,.structured-setting{padding:13px}.setup-intro{font-size:.9rem}.config-field{margin:24px 0}.field-help{font-size:.78rem}.path-base{font-size:.72rem}.setup-nav{gap:15px}.setup-tools{padding:16px 18px}}
.top-nav{align-items:center;gap:6px 22px;padding-bottom:12px;border-bottom:1px solid #e1e8ea}.top-nav a{text-decoration:none;color:#38575d;padding:4px 0}.top-nav a[aria-current=page]{color:#176b5b;border-bottom:3px solid #176b5b}.top-nav .back-link{font-weight:450;color:#6d8085;font-size:.84rem;margin-right:6px}
.overview-head{display:flex;gap:24px;align-items:center;justify-content:space-between;padding:20px 24px;margin:16px 0}.head-main{min-width:0;flex:1}.head-main>p:last-child{margin-bottom:0}.head-action{flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;gap:8px}.head-action form{margin:0}.head-status{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 4px}.headline{font-size:1.12rem;font-weight:650;color:#203b40;overflow-wrap:anywhere}.stage-line{margin:2px 0 0}.overview-head .note{margin:4px 0 0}.readonly-note{display:inline-block;padding:9px 16px;border:1px dashed #b9c9ce;border-radius:9px;color:#5d7177;font-size:.9rem}
.badge{font-size:.84rem;font-weight:650;white-space:nowrap}.tone-ok{background:#e1f1e8;color:#14593f}.tone-active{background:#e3eef8;color:#1d4f7a}.tone-warn{background:#fff1d9;color:#7a4b0c}.tone-bad{background:#fde8e6;color:#9b2a1e}.tone-neutral{background:#eceff1;color:#44585e}
.attention{border-color:#e7c48d;background:#fffaf1;border-left:5px solid #d39a3a;padding:16px 22px;margin:16px 0}.attention h2{font-size:1.02rem;margin:0 0 6px;color:#7a4b0c}.attention ul{margin:0;padding-left:20px}.attention li p{margin:0 0 4px}.attention form{margin:8px 0}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:16px 0}.stat{margin:0;padding:14px 18px;min-width:0}.stat h2{font-size:.84rem;color:#5d7177;font-weight:650;margin:0 0 4px}.stat-value{margin:0;overflow-wrap:anywhere;line-height:1.4}.stat-value strong{font-size:1.4rem;font-weight:700;color:#203b40}.stat-limit{color:#5d7177;font-size:.95rem}.stat-percent,.stat-note{margin:3px 0 0;font-size:.8rem;color:#5d7177;line-height:1.5}.stat.over{border-color:#e3a59d;background:#fffafa}.stat-percent.over,.stat-note.over{color:#a02b1f;font-weight:650}.stat .inline-error{margin:0}
progress.meter{display:block;width:100%;height:8px;margin:8px 0 0;border:0;border-radius:4px;background:#e6ecee;-webkit-appearance:none;appearance:none;overflow:hidden;color:#2e8a73}progress.meter::-webkit-progress-bar{background:#e6ecee;border-radius:4px}progress.meter::-webkit-progress-value{background:#2e8a73;border-radius:4px}progress.meter::-moz-progress-bar{background:#2e8a73}progress.meter.over{color:#c0392b}progress.meter.over::-webkit-progress-value{background:#c0392b}progress.meter.over::-moz-progress-bar{background:#c0392b}
.trail{padding:18px 22px}.trail>h2{margin-bottom:8px;font-size:1.15rem}.round{margin:8px 0;padding:0;border-radius:10px}.round>summary{padding:10px 16px}.round[open]>summary{margin:0;border-bottom:1px solid #e4e9eb}.round-title{font-weight:650}.round-meta{color:#7a8c91;font-size:.8rem;font-weight:450;margin-left:6px}.role-line{padding:12px 18px;border-bottom:1px solid #eef2f3;overflow-wrap:anywhere}.role-line:last-child{border-bottom:0}
.role-head{margin:0 0 2px;font-size:.86rem;color:#5d7177}.role{display:inline-block;font-weight:700;padding:0 8px;border-radius:6px;background:#eef3f2;color:#203b40}.role-H{background:#efeafa;color:#4b3a7a}.role-W{background:#e3eef8;color:#1d4f7a}.role-A{background:#e1f1e8;color:#14593f}.role-E{background:#f6efe1;color:#6f5216}.one-line{margin:4px 0 6px}
.facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:2px 14px;margin:6px 0;font-size:.87rem}.facts>div{display:contents}.facts dt{color:#5d7177}.facts dd{margin:0;overflow-wrap:anywhere}.hint{color:#7a8c91}.links{font-size:.84rem;margin:6px 0 0}
.token-monitor{padding:24px;border-color:#d3e1dd;box-shadow:0 3px 12px #263c4305}.token-monitor>header{margin-bottom:20px}.token-monitor h2{margin:0 0 4px;font-size:1.15rem;letter-spacing:-.02em}.token-scope{margin:0;color:#6b8081;font-size:.8rem}.token-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0}.token-metrics>div{min-width:0;padding:14px 18px;border-radius:10px;background:#f4f7f7}.token-metrics dt{color:#61777a;font-size:.77rem;line-height:1.65;margin-bottom:7px}.token-metrics dd{margin:0;color:#29484a;font-size:1.45rem;line-height:1.4;font-weight:650;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.token-metrics .token-feature{grid-column:span 2;background:#edf5f1;padding:17px 18px}.token-feature dd{color:#176b5b;font-size:1.5rem}.token-report{display:block;font-size:.74rem;font-weight:400;color:#6b8081;margin-top:6px}.token-metrics .token-note{grid-column:1/-1;display:flex;gap:8px;padding:0;background:transparent}.token-note dt,.token-note dd{margin:0;color:#7b898b;font-size:.73rem;font-weight:400;line-height:1.7}.token-note dt{flex-shrink:0}.token-note .token-report{display:inline;font-size:inherit;margin:0}
.ui-error{border-left:5px solid #a02b1f;background:#fff8f6;overflow-wrap:anywhere}.ui-error h2{color:#84261d}.ui-error code{white-space:normal}.setup-notice .ui-error{margin:0;border:0;background:transparent;padding:0}.inline-error{color:#9b2a1e;background:#fdf1ef;border:1px solid #f0c4bd;border-radius:8px;padding:9px 13px;font-size:.9rem;overflow-wrap:anywhere}.empty-note{color:#66797e}
.activity{padding:16px 22px;border-left:5px solid #3b78a8}.activity h2{font-size:1.05rem;margin:0 0 6px}.activity .messages{margin:6px 0 10px;padding-left:20px;font-size:.9rem}.activity .messages li{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.more-actions .action-row{display:flex;flex-wrap:wrap;gap:4px 14px}.more-actions .action-row form{margin:6px 0}.understanding ul{margin:0;padding-left:20px;font-size:.9rem;color:#4d6268}.instance-info{font-size:.88rem}
.secondary-link{display:inline-block;padding:10px 16px;border:1px solid #cbd8da;border-radius:9px;text-decoration:none;color:#38575d;font-weight:600;background:#fff}
.instance-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}.instance-card{background:#fff;border:1px solid #dce4e6;border-radius:12px;padding:18px 20px;min-width:0;overflow-wrap:anywhere}.instance-card h3{margin:0 0 4px;font-size:1.05rem}.instance-card .note{margin:4px 0}.instance-card .head-status{margin:8px 0}.instance-card .primary{margin-top:12px}.instance-card.failed{border-color:#f0c4bd}.instance-card .review-full-path{margin:8px 0}
.table-scroll{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}.candidate-table,.compact{font-size:.85rem}.candidate-table td,.candidate-table th,.compact td,.compact th{padding:7px 10px}.candidate-table th,.compact th{white-space:nowrap;background:#f6f9f9}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.candidates tr.extra{display:none}.toggle-all{position:absolute;opacity:0;width:1px;height:1px}.toggle-all:checked~.candidate-groups tr.extra,.candidate-group.sorted tr.extra{display:table-row}.toggle-all:checked~.toggle-label{display:none}.toggle-all:focus-visible~.toggle-label{outline:3px solid #8cbab0;outline-offset:3px}.toggle-label{margin-top:12px;font-weight:600;cursor:pointer}
.group-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;justify-content:space-between}.group-head h3{margin:18px 0 6px;overflow-wrap:anywhere}.group-head .count{color:#7a8c91;font-weight:450}.sort-button{font-size:.8rem;padding:6px 12px}.sort-note{color:#7a4b0c;font-size:.82rem;margin:0;flex-basis:100%}.mark{display:inline-block;margin-right:4px;font-weight:700}.mark-recommended{color:#a8730a}.mark-delivered{color:#14593f}.mark-failed{color:#a02b1f}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.cand-ref{display:block;font:.7rem/1.5 ui-monospace,monospace;color:#7a8c91;overflow-wrap:anywhere}.candidate-table td:nth-child(2){min-width:9em}.candidate-table{min-width:760px}.candidate-table td:nth-child(3){min-width:4.5em}.candidate-table td:nth-child(5){min-width:7em}.candidate-table td:nth-child(6),.candidate-table td:nth-child(7),.candidate-table td:nth-child(8){white-space:nowrap}.round-meta{white-space:nowrap}.stats>.inline-error{grid-column:1/-1;margin:0}.instance-card .over{color:#a02b1f;font-weight:650}
.result-card .chosen{font-size:1.5rem;margin:6px 0 8px;overflow-wrap:anywhere}.result-card .eyebrow{margin-bottom:6px}.result-card h3{margin:18px 0 6px}.result-card .facts{font-size:.92rem}.limits{padding-left:22px}.limits li{overflow-wrap:anywhere}.result-actions{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-top:18px}.result-actions .note{flex-basis:100%;margin:0}.criteria{background:#fafcfc}.material-steps{columns:2;padding-left:20px}.results-list{padding-left:0;list-style:none}.results-list>li{background:#fff;border:1px solid #dce4e6;border-radius:12px;padding:12px 18px;margin:10px 0;overflow-wrap:anywhere}.results-list .one-line{margin:2px 0 0}.settings-list{list-style:none;padding:0}.settings-list li{margin:0 0 14px}.settings-list .note{margin:2px 0 0}
.mechanism-trails{background:transparent;border:0;padding:0;margin:28px 0}.mechanism-trails>h2{margin-bottom:8px}.mechanism-trails>.note{font-size:.83rem;max-width:850px}.mechanism-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}.mechanism-card{min-width:0;padding:22px;background:#fff;border:1px solid #d5e0e1;border-radius:12px;box-shadow:0 2px 5px #263c4305;overflow-wrap:anywhere}.mechanism-card-head{display:flex;align-items:flex-start;flex-wrap:wrap;gap:9px 12px;margin-bottom:19px}.mechanism-card-head h3{flex:1;min-width:0;margin:0;font-size:1.06rem;line-height:1.5;color:#203b40}.mechanism-status{background:#edf2f3;color:#4d646a;white-space:normal;font-size:.75rem;max-width:100%}.mechanism-overview{margin:0}.mechanism-overview>div{margin:0 0 15px}.mechanism-overview>div:last-child{margin-bottom:0}.mechanism-overview dt{font-size:.75rem;font-weight:650;color:#6b8085;margin-bottom:4px}.mechanism-overview dt a{color:inherit}.mechanism-overview dd{margin:0;font-size:.88rem;line-height:1.65}.mechanism-details{min-width:0;padding:0;margin:18px 0 0;border:0;border-top:1px solid #e4ebec;border-radius:0;background:transparent}.mechanism-details>summary{padding:14px 0 0;font-size:.82rem;color:#236653}.mechanism-details[open]>summary{margin-bottom:20px}.mechanism-record-count{font-size:.74rem;font-weight:450;color:#7a8d91;white-space:nowrap;margin-left:5px}.mechanism-timeline{list-style:none;padding:0 0 0 14px;margin:0;border-left:2px solid #e1e9e9}.mechanism-event{min-width:0;padding:0 0 20px;margin:0 0 20px;border-bottom:1px solid #e5ebec;font-size:.84rem}.mechanism-event:last-child{padding-bottom:0;margin-bottom:0;border-bottom:0}.mechanism-event-head{font-size:.82rem;line-height:1.7;margin:0 0 10px}.mechanism-event h5{font-size:.8rem;margin:16px 0 7px}.mechanism-event p{white-space:pre-wrap;margin:9px 0}.mechanism-event .note{font-size:.77rem}.mechanism-event .links{font-size:.79rem}.mechanism-event .compact{min-width:500px}.mechanism-event .table-scroll{min-width:0;width:100%}
@media(max-width:750px){.mechanism-grid{grid-template-columns:minmax(0,1fr);gap:16px}.mechanism-card{padding:18px}.mechanism-card-head{margin-bottom:16px}.mechanism-card-head h3{font-size:1rem}}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.stats{grid-template-columns:1fr}.overview-head{flex-direction:column;align-items:stretch;padding:16px 18px}.head-action{align-items:stretch}.head-action .primary,.head-action button{width:100%;text-align:center}.facts{grid-template-columns:1fr}.facts dd{margin-bottom:4px}.role-line{padding:10px 12px}.round>summary{padding:10px 12px}.trail{padding:14px}.material-steps{columns:1}.top-nav{gap:4px 16px}}
.token-metrics dd.token-unavailable{font-size:.9rem;color:#7b898b;font-weight:450}
@media(max-width:560px){.token-monitor{padding:18px}.token-monitor>header{margin-bottom:16px}.token-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.token-metrics>div{padding:12px 14px}.token-metrics dd{font-size:1.3rem}.token-metrics .token-feature{grid-column:1/-1;padding:14px}.token-feature dd{font-size:1.4rem}.token-metrics .token-note{gap:7px}.token-note dd{font-size:.73rem}.token-metrics dd.token-unavailable{font-size:.9rem}}

/* Research settings and guided configuration share the existing form controls. */
.settings-intro{max-width:760px;color:#62777e;margin-bottom:18px}.settings-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}.settings-group{min-width:0;margin:0;padding:18px 22px}.settings-group h2{font-size:1.05rem;margin-bottom:12px}.settings-group .settings-list{margin:0}.settings-group .settings-list li{padding:10px 0;margin:0;border-top:1px solid #e5ebec}.settings-group .settings-list li:first-child{border:0;padding-top:0}.settings-group .settings-list li:last-child{padding-bottom:0}.settings-group .settings-list a{font-weight:650;font-size:.95rem}.settings-group .settings-list .note{font-size:.83rem;margin-top:6px;line-height:1.65}
.configuration-status{padding:20px 28px;border-left:4px solid #2e8a73;background:#f2f8f5}.configuration-status h2{font-size:1.05rem;margin:0 0 6px}.configuration-status p{margin:0;color:#4f6e64}.configuration-active>summary{font-size:.92rem}.configuration-editor .advanced-settings{margin-bottom:0}#research-config-edit-form>button{margin-top:18px}.configuration-section{padding:28px 32px}.configuration-section h2{font-size:1.15rem}.configuration-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 32px;margin:0}.configuration-facts>div{min-width:0;padding:14px 0;border-bottom:1px solid #e5ebec}.configuration-facts dt{font-size:.81rem;color:#687e83;margin-bottom:4px}.configuration-facts dd{margin:0;font-size:.95rem;font-weight:550;overflow-wrap:anywhere}.configuration-roles{font-size:.85rem;min-width:540px}.configuration-section .permission-review{min-width:560px}.configuration-roles th,.configuration-roles td{padding:12px 10px}.configuration-section>.note{font-size:.84rem}
.guidance-create{padding:30px 34px}.guidance-composer{margin-top:22px}.guidance-composer textarea[name=text]{min-height:185px;line-height:1.8}.guidance-templates{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 22px}.guidance-templates button{font-size:.81rem;padding:8px 13px;background:#f5f9f7;color:#286654;border:1px solid #d6e5df;font-weight:550}.guidance-templates button:hover{background:#e8f2ed}.guidance-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 22px}.guidance-fields>*{min-width:0}.guidance-goal-note{font-size:.84rem;padding:14px 18px;border-left:3px solid #b5cabd;background:#f3f7f4;border-radius:0 7px 7px 0}.guidance-history{margin-top:32px}.guidance-history>h2{font-size:1.15rem}.guidance-record{padding:22px 28px}.guidance-record h2,.guidance-record h3{font-size:1rem;margin:0 0 10px}.guidance-metadata{display:flex;flex-wrap:wrap;gap:6px 18px;color:#687e83;font-size:.8rem}.guidance-metadata>div{min-width:0}.guidance-metadata dt{font-size:.74rem;color:#788b90}.guidance-metadata dd{margin:2px 0 0;color:#425f65}.guidance-record+.guidance-record{border-top:1px solid #e0e8e7}.guidance-record form{margin-bottom:0}.guidance-record form button{font-size:.83rem;padding:9px 15px}
.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 22px}.config-grid>.config-field{margin:14px 0}.config-example{display:block;font-size:.79rem;color:#60777d;margin-top:8px;line-height:1.65}.configured-note{font-size:.78rem;color:#3d715e;background:#edf5f0;padding:7px 11px;border-radius:6px;overflow-wrap:anywhere}.expert-settings{background:#f8fafb;border-style:dashed}.expert-settings>summary{font-size:.87rem;color:#536c72}.expert-settings .field-key{font-size:.69rem}.expert-settings .config-node{background:white}
@media(max-width:750px){.settings-groups{grid-template-columns:minmax(0,1fr)}.settings-group{padding:18px 20px}.configuration-section,.guidance-create{padding:24px}.configuration-facts{gap:0 20px}.guidance-record{padding:21px 24px}}
@media(max-width:480px){.configuration-facts,.guidance-fields,.config-grid{grid-template-columns:minmax(0,1fr)}.settings-group,.configuration-section,.guidance-create,.guidance-record{padding:20px}.guidance-templates{gap:8px}.guidance-templates button{font-size:.78rem;padding:8px 11px}.guidance-composer>button{width:100%}.configuration-roles{font-size:.8rem}.configuration-roles th,.configuration-roles td{padding:10px 7px}}

/* Compact declaration controls are scoped to creation and research editing. */
.compact-configuration .setup-panel{padding:24px 26px}
.compact-configuration .section-heading{margin-bottom:18px}
.compact-configuration .section-heading h2{font-size:1.35rem;margin-bottom:6px}
.compact-configuration .section-heading p{font-size:.86rem}
.compact-configuration .config-field{margin:10px 0;min-width:0}
.compact-configuration .config-field>label{font-size:.83rem;margin:0 0 5px}
.compact-configuration input,.compact-configuration select,.compact-configuration textarea{font-size:.86rem;padding:8px 10px;border-radius:6px}
.compact-configuration .model-fields{grid-template-columns:2fr 1fr 1fr;gap:14px}
.compact-configuration .budget-fields{grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.compact-configuration .config-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}
.compact-configuration .config-grid-four{grid-template-columns:repeat(4,minmax(0,1fr));gap:0 12px}
.compact-configuration .config-grid>.structured-setting{grid-column:1/-1}
.compact-configuration .config-group{min-width:0;margin:16px 0 0;padding:10px 14px 8px;border:1px solid #dce5e6;border-radius:8px;background:#fff;scroll-margin-top:20px}
.compact-configuration .config-group>legend{padding:0 6px;font-size:.9rem;font-weight:650;color:#38595b}
.compact-configuration .config-group .config-field{margin:6px 0}
.compact-configuration .config-help{border:0;border-radius:0;background:transparent;padding:0;margin:5px 0 0;font-size:.8125rem;color:#60777d}
.compact-configuration .config-help>summary{font-size:.8125rem;font-weight:450;line-height:1.6}
.compact-configuration .config-help[open]>summary{margin-bottom:5px}
.compact-configuration .config-help p{margin:5px 0;font-size:.8125rem;line-height:1.65}
.compact-configuration .config-help p:last-child{margin-bottom:0}
.compact-configuration .role-settings{padding:12px 14px}
.compact-configuration .role-settings>summary{font-size:.9rem}
.compact-configuration .role-settings[open]>summary{margin-bottom:12px}
.compact-configuration .config-matrix-scroll{position:relative;width:100%;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain;border:1px solid #dce5e6;border-radius:6px}
.compact-configuration .config-matrix-scroll:focus-visible{outline:3px solid #8cbab0;outline-offset:3px}
.compact-configuration .config-role-matrix{table-layout:fixed;min-width:760px;font-size:.8125rem}
.compact-configuration .config-role-matrix .role-column{width:142px}
.compact-configuration .config-role-matrix .model-column{width:180px}
.compact-configuration .config-role-matrix th,.compact-configuration .config-role-matrix td{padding:8px 6px;vertical-align:middle}
.compact-configuration .config-role-matrix thead th{background:#f2f7f5;color:#4d6869;line-height:1.5}
.compact-configuration .config-role-matrix thead tr:first-child th{border-bottom-color:#d0dfdb}
.compact-configuration .config-role-matrix tbody th{font-weight:550;background:#fafcfc;line-height:1.5}
.compact-configuration .config-role-matrix tbody tr:last-child>*{border-bottom:0}
.compact-configuration .config-role-matrix .config-field{margin:0}
.compact-configuration .config-role-matrix input{padding:7px 8px;font-size:.8125rem}
.compact-configuration .config-role-matrix .check-label{font-size:.8125rem;margin:5px 0 0}
.compact-configuration .config-role-matrix td:nth-last-child(2),.compact-configuration .config-role-matrix thead tr:first-child th:last-child{border-left:1px solid #d0dfdb}
.compact-configuration .config-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}
.compact-configuration .role-settings-help{margin-top:8px}
.compact-configuration .summary-note{font-size:.8125rem;margin-left:10px}
.compact-configuration .configured-note{font-size:.8125rem;padding:3px 7px;margin-left:8px}
.compact-configuration .permission-settings,.compact-configuration .advanced-settings{padding:13px 16px;margin-top:16px}
.compact-configuration .advanced-category,.compact-configuration .structured-setting{padding:12px 14px;margin:10px 0}
.compact-configuration .advanced-category>.note{margin:8px 0;font-size:.8125rem}
.compact-configuration .permission-settings>.note{font-size:.8125rem;margin:8px 0}
.compact-configuration .field-label{font-size:.86rem;margin:0 0 6px;font-weight:650}
.compact-configuration .check-label{font-size:.8125rem;margin:8px 0}
.compact-configuration .config-string-row{margin:7px 0}
.compact-configuration .setup-actions{margin-top:20px;padding-top:16px}
.compact-configuration .path-heading{margin-top:20px}
@media(max-width:750px){.compact-configuration .setup-panel{padding:20px}.compact-configuration .config-grid-four{grid-template-columns:repeat(2,minmax(0,1fr))}.compact-configuration .model-fields{grid-template-columns:2fr 1fr 1fr}.compact-configuration .model-fields .config-field:first-child{grid-column:auto}.compact-configuration .budget-fields{grid-template-columns:repeat(3,minmax(0,1fr))}.compact-configuration .summary-note{display:block;margin:4px 0 0}.compact-configuration .configured-note{display:inline-block;margin:4px 0 0 8px}}
@media(max-width:480px){.compact-configuration .setup-panel{padding:16px}.compact-configuration .model-fields,.compact-configuration .budget-fields,.compact-configuration .config-grid{grid-template-columns:minmax(0,1fr)}.compact-configuration .config-grid-four{grid-template-columns:repeat(2,minmax(0,1fr))}.compact-configuration .config-group{padding:8px 10px}.compact-configuration .permission-settings,.compact-configuration .advanced-settings{padding:12px}.compact-configuration .advanced-category,.compact-configuration .structured-setting{padding:10px}.compact-configuration .config-role-matrix{min-width:760px}.compact-configuration .config-group:target{border-color:#4b907c}}

/* Categories keep advanced settings compact until the relevant group is opened. */
.compact-configuration .advanced-settings>.note{font-size:.8125rem;margin:8px 0 14px}
.compact-configuration .advanced-settings .advanced-category{min-width:0;border:0;border-top:1px solid #dce5e6;border-radius:0;padding:14px 0 0;margin:16px 0 0;background:transparent}
.compact-configuration .advanced-settings .advanced-category>summary{font-size:.9rem;margin:0;color:#38595b}
.compact-configuration .advanced-settings .advanced-category>.note{margin:4px 0 8px;font-size:.8125rem}
.compact-configuration .advanced-settings .config-grid-four{grid-template-columns:repeat(4,minmax(0,1fr));gap:0 14px}
.compact-configuration .advanced-settings .config-field{margin:6px 0}
.compact-configuration .advanced-settings .config-group,.compact-configuration .advanced-settings .known-settings{min-width:0;border:0;border-top:1px solid #e2e9e8;border-radius:0;padding:7px 0 0;margin:12px 0 0;background:transparent}
.compact-configuration .advanced-settings .config-group>legend,.compact-configuration .advanced-settings .known-settings>legend{padding:0 8px 0 0;font-size:.85rem;font-weight:650;color:#49676a}
.compact-configuration .advanced-settings .known-settings>.check-label{margin:3px 0 7px}
.compact-configuration .advanced-settings .known-settings>.config-help{margin-bottom:6px}
.compact-configuration .advanced-settings .structured-setting:not(.known-settings){border:1px solid #dce5e6;background:#fff;padding:10px 12px;margin:10px 0}
.compact-configuration .advanced-settings .structured-setting>summary{font-size:.85rem}
.compact-configuration .advanced-settings .structured-setting[open]>summary{margin-bottom:9px}
.compact-configuration .advanced-settings .expert-settings{padding:8px 10px;margin:8px 0;border-radius:5px}
.compact-configuration .advanced-settings .expert-settings>summary{font-size:.8125rem}
.compact-configuration .advanced-settings .expert-settings[open]>summary{margin-bottom:8px}
.compact-configuration .advanced-settings .structured-setting .note,.compact-configuration .advanced-settings .expert-settings .note{font-size:.8125rem;margin:6px 0}
.compact-configuration .advanced-settings .config-list-setting{grid-column:span 2}
.compact-configuration .advanced-settings .config-list-setting>.field-label{margin:0 0 4px}
.compact-configuration .advanced-settings .config-list-setting>.check-label{margin:4px 0}
.compact-configuration .advanced-settings .config-list-setting>textarea{min-height:54px}
.compact-configuration .advanced-settings .config-string-row textarea{min-height:38px}
.compact-configuration .advanced-settings button{font-size:.8125rem;padding:6px 10px}
.compact-configuration .advanced-settings .config-list-setting>.config-string-add{margin-top:6px}
.compact-configuration .advanced-settings .config-node{display:grid;grid-template-columns:minmax(0,1fr) 112px minmax(0,1.3fr) auto;gap:5px 10px;align-items:end;padding:8px 0;margin:0;border:0;border-bottom:1px solid #e2e9e8;border-radius:0;background:transparent}
.compact-configuration .advanced-settings .config-node>label{min-width:0;margin:0;font-size:.8125rem;font-weight:450}
.compact-configuration .advanced-settings .config-node>label>input,.compact-configuration .advanced-settings .config-node>label>select{height:38px;line-height:1.4}
.compact-configuration .advanced-settings .config-node>.config-node-key{grid-column:1;grid-row:1}
.compact-configuration .advanced-settings .config-node>.config-node-type{grid-column:2;grid-row:1}
.compact-configuration .advanced-settings .config-node>.config-value{grid-column:3;grid-row:1}
.compact-configuration .advanced-settings .config-node>.config-remove{grid-column:4;grid-row:1;white-space:nowrap;margin-bottom:1px}
.compact-configuration .advanced-settings .config-node>.config-children{grid-column:1/-1;margin:0 0 0 12px;min-width:0;padding-left:12px;border-left:2px solid #e1ebe7}
.compact-configuration .advanced-settings .config-node>.config-add{grid-column:1/-1;justify-self:start}
@media(max-width:900px){.compact-configuration .advanced-settings .config-grid-four{grid-template-columns:repeat(3,minmax(0,1fr))}.compact-configuration .advanced-settings .config-node{grid-template-columns:minmax(0,1fr) 100px minmax(0,1fr) auto;gap:5px 7px}}
@media(max-width:650px){.compact-configuration .advanced-settings .config-grid-four{grid-template-columns:repeat(2,minmax(0,1fr))}.compact-configuration .advanced-settings .config-node{grid-template-columns:minmax(0,1fr) 100px auto}.compact-configuration .advanced-settings .config-node>.config-value{grid-column:1/3;grid-row:2}.compact-configuration .advanced-settings .config-node>.config-remove{grid-column:3;grid-row:1}.compact-configuration .advanced-settings .config-node>.config-children{margin-left:0;padding-left:8px}.compact-configuration .advanced-settings .config-node>.config-add{grid-row:auto}}
@media(max-width:480px){.compact-configuration .advanced-settings .config-grid-four{grid-template-columns:minmax(0,1fr)}.compact-configuration .advanced-settings .config-list-setting{grid-column:auto}.compact-configuration .advanced-settings .advanced-category{padding:12px 0 0}.compact-configuration .advanced-settings .config-node{grid-template-columns:minmax(0,1fr) 84px auto;gap:5px}.compact-configuration .advanced-settings .config-node>.config-remove{padding:6px}.compact-configuration .advanced-settings .config-node input,.compact-configuration .advanced-settings .config-node select{padding:7px 6px}}


'''

PROGRESS_SCRIPT = """
const connection = document.querySelector('[data-live-url]');
const panel = document.querySelector('[data-progress]');
let timer, generation = 0, cursor = panel?.dataset.cursor || '', progressURL = panel?.dataset.progress || '';
let rows = panel ? JSON.parse(panel.querySelector('[data-raw-events]').textContent) : [];
let source, sessionCheck = false, authorizationLost = false;
async function responseProblem(response, fallback) {
  let value;
  try { value = await response.json(); } catch (_) {}
  const problem = value?.error;
  return problem && typeof problem === 'object' ? [problem.title, problem.cause, problem.hint,
    problem.field ? '相关字段：' + problem.field : '', problem.reference ? '错误编号：' + problem.reference : ''].filter(Boolean).join(' · ') : fallback;
}
function stopForAuthorization(message) {
  authorizationLost = true; generation++; window.clearTimeout(timer); source?.close(); source = null;
  if (connection) { connection.textContent = message; connection.dataset.state = 'expired'; }
  if (panel) panel.querySelector('[data-stream-status]').textContent = message;
}
async function checkSession() {
  if (!connection || sessionCheck || authorizationLost) return;
  sessionCheck = true;
  try {
    const response = await fetch(connection.dataset.liveUrl.slice(0, -4) + 'session', {cache:'no-store', credentials:'same-origin'});
    if (response.status === 403) stopForAuthorization(await responseProblem(response,
      '浏览会话已失效，请回到启动终端，使用本次 management_url 重新打开；已显示内容和已填输入保留。'));
    else if (!response.ok) connection.textContent = await responseProblem(response, '工作台状态读取失败，请检查工作台。');
  } catch (_) { /* A network interruption retains the ordinary read-only reconnect. */ }
  finally { sessionCheck = false; }
}
function append(item) {
  const row = document.createElement('article'); row.className = 'event';
  const text = document.createElement('p');
  const states = {started:'开始', updated:'进行中', completed:'已完成', failed:'失败'};
  if (item.type === 'message') text.textContent = item.text;
  else if (item.type === 'tool') text.textContent = '工具 ' + item.tool + ' · ' + states[item.status];
  else if (item.type === 'session.started') text.textContent = '研究助手已连接';
  else if (item.type === 'turn.completed') text.textContent = '本次助手工作已结束，等待阶段结论';
  else if (item.type === 'usage') text.textContent = '用量更新：输入 ' + item.usage.input_tokens + ' / 输出 ' + item.usage.output_tokens + ' tokens';
  else return;
  row.appendChild(text);
  if (item.omitted_characters) { const note = document.createElement('small'); note.textContent = '本条公开文本已截短'; row.appendChild(note); }
  const events = panel.querySelector('[data-events]'); events.appendChild(row);
  while (events.children.length > 200) events.removeChild(events.firstChild);
}
function tokenDetails(details) {
  const slot = document.querySelector('[data-token-monitor] [data-token-details]');
  if (!slot) return;
  const list = document.createElement('dl'); list.className = 'token-metrics';
  for (const item of details || []) {
    const row = document.createElement('div'), label = document.createElement('dt'), value = document.createElement('dd');
    row.className = ['计量状态','观测限制'].includes(item.label) ? 'token-note' : (item.label === '缓存命中率' || item.label.startsWith('最近一次上报上下文') ? 'token-feature' : 'token-count');
    label.textContent = item.label;
    if (item.value.startsWith('不可用')) value.className = 'token-unavailable';
    const separator = row.className === 'token-note' ? -1 : item.value.indexOf('；');
    value.textContent = separator < 0 ? item.value : item.value.slice(0, separator);
    if (separator >= 0) { const report = document.createElement('span'); report.className = 'token-report'; report.textContent = item.value.slice(separator + 1); value.appendChild(report); }
    row.append(label, value); list.appendChild(row);
  }
  slot.replaceChildren(list);
}
async function poll(selectedGeneration) {
  if (!progressURL || selectedGeneration !== generation || authorizationLost) return;
  const status = panel.querySelector('[data-stream-status]');
  try {
    const response = await fetch(progressURL + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), {cache:'no-store'});
    if (selectedGeneration !== generation) return;
    if (!response.ok) {
      const message = await responseProblem(response, response.status === 403
        ? '浏览会话已失效，请从终端本次 management_url 重新打开工作台。' : '公开进度读取失败，请重新打开此阶段页面。');
      if (selectedGeneration !== generation) return;
      if (response.status === 403) { stopForAuthorization(message); return; }
      status.textContent = message;
      if (response.status >= 400 && response.status < 500) return;
      const error = new Error(message); error.publicProblem = true; throw error;
    }
    const value = await response.json();
    if (selectedGeneration !== generation) return;
    status.textContent = value.progress_label;
    tokenDetails(value.token_details);
    for (const item of value.events) { rows.push(item); append(item); }
    if (rows.length > 200) rows.splice(0, rows.length - 200);
    panel.querySelector('[data-raw-events]').textContent = JSON.stringify(rows, null, 2);
    if (value.restart_required) { status.textContent += '；请重新打开此进度页以读取新来源'; return; }
    cursor = value.cursor || cursor;
  } catch (error) { if (selectedGeneration === generation) status.textContent = (error.publicProblem ? error.message : '进度连接暂不可用') + '；正在重新连接，已显示内容保留'; }
  if (selectedGeneration === generation) timer = window.setTimeout(() => poll(selectedGeneration), 2000);
}
function follow(current) {
  if (!panel?.dataset.followLatest || authorizationLost) return;
  const next = current?.url || '';
  if (next === progressURL) {
    if (!current) {
      panel.querySelector('[data-stream-status]').textContent = '尚无研究阶段；状态会自动更新';
      document.querySelector('[data-token-monitor] [data-token-scope]').textContent = '当前阶段 · 等待助手用量';
    }
    return;
  }
  generation++; window.clearTimeout(timer); progressURL = next; cursor = ''; rows = [];
  panel.querySelector('[data-events]').replaceChildren();
  panel.querySelector('[data-raw-events]').textContent = '[]';
  tokenDetails([]);
  document.querySelector('[data-token-monitor] [data-token-scope]').textContent = current ? '第 ' + current.step + ' 阶段 · attempt ' + current.attempt + ' · 本次助手用量' : '当前阶段 · 等待助手用量';
  panel.querySelector('[data-progress-heading]').textContent = current ? '第 ' + current.step + ' 阶段 · ' + current.role + ' · 公开工作' : '当前助手的公开工作';
  panel.querySelector('[data-stream-status]').textContent = current ? '正在读取当前阶段状态…' : '尚无研究阶段；状态会自动更新';
  poll(generation);
}
if (panel && !panel.dataset.followLatest) poll(generation);
const rendered = new Map();
// Editable forms outside these slots are never replaced. A correction-note
// form inside a live action slot also keeps its user's draft.
document.addEventListener('input', event => {
  const slot = event.target.closest('[id^="live-"]');
  const form = event.target.closest('form');
  if (slot && form) form.dataset.liveDraft = 'true';
});
function connect() {
  if (!connection || source || authorizationLost) return;
  source = new EventSource(connection.dataset.liveUrl);
  source.addEventListener('snapshot', event => {
    if (authorizationLost) return;
    try {
      const value = JSON.parse(event.data);
      for (const [id, markup] of Object.entries(value.fragments)) {
        const slot = document.getElementById(id);
        if (!slot || rendered.get(id) === markup) continue;
        const drafts = Array.from(slot.querySelectorAll('form[data-live-draft]'));
        // Preserve a reader's disclosures only for the same round or instance.
        const disclosures = new Map(Array.from(slot.querySelectorAll('details[data-details-key]')).map(item => [item.dataset.detailsKey, item.open]));
        const focused = slot.contains(document.activeElement) ? document.activeElement : null;
        const focusKey = focused?.dataset.liveFocus;
        slot.innerHTML = markup; rendered.set(id, markup);
        for (const item of slot.querySelectorAll('details[data-details-key]')) if (disclosures.has(item.dataset.detailsKey)) item.open = disclosures.get(item.dataset.detailsKey);
        for (const draft of drafts) {
          const action = draft.getAttribute('action');
          const trigger = draft.querySelector('[name=ack_correction]')?.value;
          const replacement = Array.from(slot.querySelectorAll('form')).find(form =>
            form.getAttribute('action') === action && form.querySelector('[name=ack_correction]')?.value === trigger);
          if (replacement) {
            // Keep the actual editable controls and their draft text. Hidden
            // bindings below receive this snapshot's current nonce/CSRF.
            replacement.replaceWith(draft);
          } else {
            // The action ceased to apply. Preserve its text separately while
            // the live hero continues following authoritative state.
            let shelf = document.getElementById('live-drafts');
            if (!shelf) { shelf = document.createElement('section'); shelf.id = 'live-drafts'; const note = document.createElement('p'); note.textContent = '研究状态已变化；以下未提交说明已保留，请按最新状态操作。'; shelf.appendChild(note); connection.insertAdjacentElement('afterend', shelf); }
            draft.querySelectorAll('button[type=submit]').forEach(button => { button.disabled = true; });
            shelf.appendChild(draft);
          }
        }
        // Replaced timeline controls regain focus by exact component identity.
        // No focus is moved when the reader was elsewhere or the node vanished.
        const restoredFocus = focused?.isConnected ? focused : focusKey && Array.from(slot.querySelectorAll('[data-live-focus]')).find(item => item.dataset.liveFocus === focusKey);
        restoredFocus?.focus({preventScroll:true});
      }
      for (const form of document.querySelectorAll('form[action]')) {
        const action = form.getAttribute('action').split('/').pop();
        if (!value.csrf[action]) continue;
        const nonce = form.querySelector('[name="nonce"]'), csrf = form.querySelector('[name="csrf"]');
        if (nonce && csrf) { nonce.value = value.nonce; csrf.value = value.csrf[action]; }
      }
      follow(value.current);
      connection.textContent = '已连接 · 状态自动同步'; connection.dataset.state = 'connected';
    } catch (_) { connection.textContent = '状态暂不可用，正在重连'; connection.dataset.state = 'offline'; }
  });
  source.addEventListener('unavailable', () => { connection.textContent = '状态暂不可用，正在自动重连'; connection.dataset.state = 'offline'; checkSession(); });
  source.onerror = () => { connection.textContent = '连接中断，正在自动重连；已填写内容保留'; connection.dataset.state = 'offline'; checkSession(); };
}
connect();
window.addEventListener('pagehide', () => { source?.close(); source = null; generation++; window.clearTimeout(timer); });
window.addEventListener('pageshow', event => { if (event.persisted) { connect(); if (panel && progressURL) poll(generation); } });
// Candidate tables: client-side ordering stays inside one evaluation_key group.
for (const button of document.querySelectorAll('[data-sort-group]')) {
  button.addEventListener('click', () => {
    const group = button.closest('.candidate-group'), body = group.querySelector('tbody');
    const next = button.dataset.state === 'desc' ? 'asc' : button.dataset.state === 'asc' ? 'order' : 'desc';
    const value = row => row.dataset.metric === '' ? null : Number(row.dataset.metric);
    const rows = Array.from(body.rows).sort((a, b) => {
      const x = value(a), y = value(b);
      if (next === 'order' || (x === null && y === null)) return a.dataset.order - b.dataset.order;
      if (x === null) return 1;
      if (y === null) return -1;
      return next === 'desc' ? y - x || a.dataset.order - b.dataset.order : x - y || a.dataset.order - b.dataset.order;
    });
    body.append(...rows);
    button.dataset.state = next;
    group.classList.toggle('sorted', next !== 'order');
    group.querySelector('[data-sort-note]').hidden = next === 'order';
    button.textContent = next === 'desc' ? '指标降序 · 点击改为升序' : next === 'asc' ? '指标升序 · 点击恢复观察顺序' : '在此口径内按指标排序';
  });
}
"""
SCRIPT_SOURCE = "'sha256-" + base64.b64encode(hashlib.sha256(PROGRESS_SCRIPT.encode()).digest()).decode() + "'"
STYLE_SOURCE = "'sha256-" + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode() + "'"
CSP = "default-src 'none'; style-src " + STYLE_SOURCE + "; script-src " + SCRIPT_SOURCE + " " + CONFIG_SCRIPT_CSP + " " + GUIDANCE_SCRIPT_CSP + " " + translation_client.CSP + "; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"


def escape(value):
    return html.escape(str(value), quote=True)


def page(title, body, *, live=False):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)
        +'</title><style>'+CSS+'</style></head><body><main><div class="app-brand"><span class="brand-mark" aria-hidden="true">S</span>SciDiscovery<span class="brand-caption">研究工作空间</span></div><h1>'+escape(title)+'</h1>'
        +translation_client.TOOLBAR+body+'</main><script>'+translation_client.SCRIPT+'</script>'+('<script>'+PROGRESS_SCRIPT+'</script>' if live else '')+'</body></html>').encode()


def excerpt(value, limit):
    text = value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,allow_nan=False)
    omitted = max(0,len(text)-limit)
    return ('<div class="finding">'+(ui.translatable(text[:limit]) if isinstance(value, str) else escape(text[:limit]))+'</div>'
        + ('<p class="note">'+str(omitted)+' 个字符未展开；可下载完整结论原文。</p>' if omitted else ''))


def result(step, job, payload, seal):
    base='/nodes/'+str(step)
    nav=('<nav><a href="/results">阶段结论</a><a href="'+base+'">第 '+str(step)
        +'</a><a href="'+base+'/science">下载完整结论原文</a><a href="'+base+'/materials">'
        +str(len(seal['materials']))+' 份研究文件</a></nav>')
    return page('阶段结论 · 第 '+str(step),nav+result_body(step,job,payload,seal))


def result_body(step, job, payload, seal):
    """The complete escaped original-result presentation, without page chrome."""
    base='/nodes/'+str(step)
    body=('<p class="links"><a href="'+base+'/science">下载完整结论原文</a> · <a href="'+base+'/materials">'
        +str(len(seal['materials']))+' 份本阶段研究文件</a></p>')
    body+='<section><h2>结论说明</h2><p>本阶段已留下结论原文。是否达到研究目标，请结合研究者写下的发现、局限和待解决问题判断。</p></section>'
    body+='<p class="note">已记录的阶段 '+str(step)+' · '+escape(job['role'])+'. 记录完成不等于科学结论已获认可。</p>'
    comparison=((job.get('history') or {}).get('view') or {}).get('candidates')
    comparison=comparison if isinstance(comparison,dict) else {}
    body+='<section><h2>研究发现</h2>'+excerpt(payload.get('summary','原文未提供'),512)
    body+=excerpt(payload.get('scientific_result',payload.get('summary','原文未提供')),4096)+'</section>'
    body+='<div class="cards"><section><h2>候选方案的已有观察</h2><p class="note">以下是本阶段开始时已有的观察，后续证据未计入。指标需要结合完整结论判断。</p>'
    facts=comparison.get('candidate_facts') or []
    for fact in facts:
        body+='<article><h3>'+escape(fact.get('id','未注明的候选方案'))+'</h3>'
        body+='<p>评估：'+escape(fact.get('evaluation_key','原文未注明'))+'</p>'
        body+='<p class="metric">'+escape(fact.get('metric','未知'))+'</p><p>指标方向：'+escape(fact.get('direction','unknown'))+'</p>'
        if fact.get('evaluation_key_truncated'):
            body+='<p class="gap">原始摘要中的评估标识已截短。</p>'
        body+='</article>'
    if not facts:
        body+='<p class="gap">本阶段的输入摘要未列出候选方案观察，可查看完整原文。</p>'
    if comparison.get('candidate_facts_omitted_count'):
        body+='<p class="gap">'+str(comparison['candidate_facts_omitted_count'])+'条候选方案观察未在摘要中展开，请查看完整原文。</p>'
    body+='<p>观察覆盖范围：'+escape(comparison.get('coverage','unknown'))+'</p></section>'
    body+='<section><h2>研究者的建议</h2>'
    ordinary_a=job['role']=='A' and not job.get('formal_review') and payload.get('next_action')!='audit'
    recommended=payload.get('candidate_recommendation') if ordinary_a else None
    if ordinary_a:
        body+=('<p>本阶段综合评估建议：<strong>'+escape(recommended)+'</strong></p>' if recommended is not None else
            '<p class="gap">本次综合评估未推荐候选方案。</p>')
    else:
        body+='<p class="gap">此阶段没有普通综合评估的候选推荐。</p>'
    previous=comparison.get('a_recommended')
    if previous:
        body+='<details><summary>本阶段开始前的综合评估建议</summary><p>A'+escape(previous['author_step'])+': '+escape(previous['candidate_id'])+'</p></details>'
    body+='<p class="note">研究者的建议、指标观察、交付情况和正式评审各自保留。</p></section></div>'
    body+='<section><h2>局限与待解决问题</h2>'
    limits=payload.get('limitations') or []
    for value in limits[:6]:body+=excerpt(value,1200)
    if len(limits)>6:body+='<p>'+str(len(limits)-6)+'条局限未展开，请下载完整结论原文。</p>'
    if not limits:body+='<p>原文未单列局限。</p>'
    if payload.get('remaining_question') is not None:body+='<h3>待解决问题</h3>'+excerpt(payload['remaining_question'],2048)
    body+='</section>'
    fields=('scope','coverage','candidate_count','latest_a_step','view_status',
        'candidate_facts_omitted_count','audits_omitted_count',
        'failed_candidate_count','unknown_candidate_count','candidate_alerts','candidate_alerts_omitted_count',
        'public_audits','limitation')
    public={key:comparison[key] for key in fields if key in comparison}
    body+='<details><summary>高级：比较范围与未展开内容</summary><pre>'+escape(json.dumps(public,ensure_ascii=False,indent=2))+'</pre>'
    body+='<p class="note">摘要未展开的内容仍可在完整结论和研究文件中查看。</p></details>'
    return body
