// Review layout preferences are separate from prose drafts and task state.
const reviewPanels={context:true,decisions:true};
let reviewBreakpoint="";
function restoreReviewPanels(){
  const breakpoint=innerWidth<=768?"phone":innerWidth<=1200?"tablet":"desktop";
  if(breakpoint===reviewBreakpoint)return;
  reviewBreakpoint=breakpoint;
  let saved={};try{saved=JSON.parse(localStorage.getItem("studio-review-panels:"+breakpoint)||"{}")}catch{}
  reviewPanels.context=saved.context??breakpoint==="desktop";
  reviewPanels.decisions=saved.decisions??breakpoint==="desktop";
  for(const [key,id] of Object.entries({context:"Context",decisions:"Decisions"})){
    const panel=$("review"+id),button=$("toggleReview"+id);
    if(!reviewPanels[key]&&panel.contains(document.activeElement))button.focus();
    $("layout").classList.toggle("hide-"+key,!reviewPanels[key]);
    button.setAttribute("aria-expanded",String(reviewPanels[key]));
  }
}
for(const [key,id] of Object.entries({context:"Context",decisions:"Decisions"})){
  $("toggleReview"+id).onclick=()=>{
    reviewPanels[key]=!reviewPanels[key];
    $("layout").classList.toggle("hide-"+key,!reviewPanels[key]);
    $("toggleReview"+id).setAttribute("aria-expanded",String(reviewPanels[key]));
    try{localStorage.setItem("studio-review-panels:"+reviewBreakpoint,JSON.stringify(reviewPanels))}catch{}
    if(reviewPanels[key]&&reviewBreakpoint!=="desktop")$("review"+id).scrollIntoView({block:"start"});
  };
}
restoreReviewPanels();window.addEventListener("resize",restoreReviewPanels);
const reviewStatusLabel=value=>({
  awaiting_human_story_review:"等待人工审稿决定",awaiting_human_author_revision:"等待实质修改",
  reviews_pending:"等待独立审稿",review_bundle_ready:"审稿意见已汇总",repair_required:"需要修改后重新审稿",
  redirect_required:"需要调整创作方向",accepted:"当前审稿已接受",awaiting_finalize:"等待确认定稿",
  awaiting_human_candidate:"请编辑并保存完整修改稿",validation_pending:"修改稿已保存，等待复核",
  semantic_review_pending:"等待独立语义复核",validated_for_submit:"终稿已锁定，可以提交复审",
  submitted:"人工终稿已提交",complete:"人工终稿已提交，当前版本只读",stale:"来源已变化，需要重新核对",
}[value]||"请查看本章审稿详情");
