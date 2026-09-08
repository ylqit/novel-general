// Readable local Markdown subset. HTML, images and links remain inert text.
const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function inline(text){
 return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map(part=>part.startsWith("`")&&part.endsWith("`")?`<code>${escape(part.slice(1,-1))}</code>`:part.startsWith("**")&&part.endsWith("**")?`<strong>${escape(part.slice(2,-2))}</strong>`:escape(part)).join("");
}
export function markdownView(text){
 const lines=String(text).replace(/\r\n?/g,"\n").split("\n"),blocks=[];
 const cells=line=>line.trim().replace(/^\||\|$/g,"").split("|").map(cell=>cell.trim());
 for(let index=0;index<lines.length;){
  const line=lines[index];
  if(!line.trim()){index++;continue;}
  if(/^```/.test(line)){
   const code=[];index++;while(index<lines.length&&!/^```/.test(lines[index]))code.push(lines[index++]);
   if(index<lines.length)index++;blocks.push(`<pre><code>${escape(code.join("\n"))}</code></pre>`);continue;
  }
  const heading=line.match(/^(#{1,6})\s+(.+)$/);
  if(heading){const level=Math.min(heading[1].length+1,6);blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`);index++;continue;}
  if(line.includes("|")&&index+1<lines.length&&cells(lines[index+1]).every(cell=>/^:?-{3,}:?$/.test(cell))){
   const header=cells(line);index+=2;const rows=[];
   while(index<lines.length&&lines[index].trim().includes("|"))rows.push(cells(lines[index++]));
   blocks.push(`<div class="document-table"><table><thead><tr>${header.map(cell=>`<th scope="col">${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${header.map((_,i)=>`<td>${inline(row[i]||"")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);continue;
  }
  if(/^\s*[-*+]\s+/.test(line)||/^\s*\d+[.)]\s+/.test(line)){
   const ordered=/^\s*\d/.test(line),pattern=ordered?/^\s*\d+[.)]\s+/:/^\s*[-*+]\s+/;
   const items=[];while(index<lines.length&&pattern.test(lines[index]))items.push(inline(lines[index++].replace(pattern,"")));
   const tag=ordered?"ol":"ul";blocks.push(`<${tag}>${items.map(item=>`<li>${item}</li>`).join("")}</${tag}>`);continue;
  }
  if(/^>\s?/.test(line)){blocks.push(`<blockquote>${inline(line.replace(/^>\s?/,""))}</blockquote>`);index++;continue;}
  const paragraph=[line];index++;
  while(index<lines.length&&lines[index].trim()&&!/^(#{1,6}\s|```|>\s?|\s*[-*+]\s|\s*\d+[.)]\s)/.test(lines[index])&&!(lines[index].includes("|")&&index+1<lines.length&&cells(lines[index+1]).every(cell=>/^:?-{3,}:?$/.test(cell))))paragraph.push(lines[index++]);
  blocks.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
 }
 return blocks.join("\n");
}
