/* Reproducible code identity for external browser evidence, excluding novels/logs. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const repository=path.resolve(__dirname,'..');
exports.captureBuildMetadata=()=>{
 try{
  const head=execFileSync('git',['rev-parse','HEAD'],{cwd:repository,encoding:'utf8',windowsHide:true}).trim();
  const output=execFileSync('git',['ls-files','--cached','--others','--exclude-standard','-z','--','src','templates','config','scripts','docs','resource-manifest.json','pyproject.toml','README.md'],{cwd:repository,encoding:'utf8',windowsHide:true,maxBuffer:4*1024*1024});
  const digest=crypto.createHash('sha256');let count=0;
  for(const relative of [...new Set(output.split('\0').filter(Boolean))].sort()){
   const file=path.join(repository,relative);digest.update(relative+'\0');
   if(fs.existsSync(file)&&fs.statSync(file).isFile()){digest.update(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'));count++;}else digest.update('missing');
   digest.update('\0');
  }
  return {commit:head,code_sha256:digest.digest('hex'),source_file_count:count,captured_at:new Date().toISOString()};
 }catch(error){return {commit:null,code_sha256:null,captured_at:new Date().toISOString(),error:error.message.split('\n')[0]};}
};
