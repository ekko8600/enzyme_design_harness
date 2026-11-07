"""Offline AST extraction. Never imports or runs a scientific entrypoint."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIC_VALUES = {}

def literal(node, default=None):
    if isinstance(node,ast.Name):return STATIC_VALUES.get(node.id,default)
    if isinstance(node,ast.Dict):
        keys=[literal(k) for k in node.keys]
        if all(isinstance(k,(str,int,float,bool)) for k in keys):return dict(zip(keys,[literal(v) for v in node.values]))
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='keys' and not node.args:
        value=literal(node.func.value)
        if isinstance(value,dict):return list(value)
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'sorted','list','tuple'} and len(node.args)==1 and not node.keywords:
        value=literal(node.args[0])
        if isinstance(value,(list,tuple,dict,set)) and len(value)<=10000:
            return sorted(value) if node.func.id=='sorted' else list(value)
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='range' and all(isinstance(n,ast.Constant) and isinstance(n.value,int) for n in node.args):
        values=range(*(n.value for n in node.args))
        return list(values) if len(values)<=10000 else default
    try: return ast.literal_eval(node)
    except (ValueError, TypeError): return default

def path_kind(name):
    n=name.lower().replace('-', '_')
    if any(w in n for w in ('output','out_dir','out_file','save_path','save_dir','workdir','work_dir','cache_dir')) or n in {'out','o'}: return 'output'
    if any(w in n for w in ('input','file','fasta','pdb','sdf','trajectory','topology','prmtop','coordinates','checkpoint','restart','structure_path','csv','manifest')): return 'input'
    if n.endswith(('_dir','_path')): return 'resource'
    return 'scalar'

def extract(path):
    STATIC_VALUES.clear()
    tree=ast.parse(path.read_text())
    for statement in tree.body:
        if isinstance(statement,ast.Assign):
            value=literal(statement.value)
            if value is not None:
                for target in statement.targets:
                    if isinstance(target,ast.Name):STATIC_VALUES[target.id]=value

    class ExpandLoops(ast.NodeTransformer):
        def visit_For(self,node):
            values=literal(node.iter)
            if isinstance(node.target,ast.Name) and isinstance(values,(list,tuple)) and all(isinstance(v,str) for v in values):
                import copy
                expanded=[]
                for value in values:
                    assigned={n.id for statement in node.body for n in ast.walk(statement) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
                    class Bind(ast.NodeTransformer):
                        def visit_Name(self,n):return ast.copy_location(ast.Constant(value),n) if n.id==node.target.id and isinstance(n.ctx,ast.Load) else ast.copy_location(ast.Name(id=n.id+'__'+value.replace('-','_'),ctx=n.ctx),n) if n.id in assigned else n
                    expanded += [Bind().visit(copy.deepcopy(n)) for n in node.body]
                return expanded
            return self.generic_visit(node)
    tree=ExpandLoops().visit(tree); owners={} ; arguments=[]; groups=[]; commands=[]; parent_commands={}; group_parsers={}
    # Ordered source traversal distinguishes separate subparsers; AST walk alone does not.
    nodes=sorted(ast.walk(tree),key=lambda n:(getattr(n,'lineno',0),getattr(n,'col_offset',0)))
    for n in nodes:
        if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call):
            c=n.value
            if isinstance(c.func,ast.Attribute):
                method=c.func.attr; owner=ast.unparse(c.func.value)
                if method=='add_subparsers':
                    for t in n.targets:owners[ast.unparse(t)]=owners.get(owner,'')
                elif method=='add_parser' and c.args and isinstance(c.args[0],ast.Constant):
                    name=(owners.get(owner,'')+' '+c.args[0].value).strip(); commands.append(name)
                    for t in n.targets:owners[ast.unparse(t)]=name
                elif method in {'add_mutually_exclusive_group','add_argument_group'}:
                    for t in n.targets: owners[ast.unparse(t)]=owners.get(owner,'')
                    if method=='add_mutually_exclusive_group':groups.append({'owner':ast.unparse(n.targets[0]),'command':owners.get(owner,''),'required':any(k.arg=='required' and literal(k.value) for k in c.keywords),'arguments':[]})
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_parser' and n.args and isinstance(n.args[0],ast.Constant):
            name=(owners.get(ast.unparse(n.func.value),'')+' '+n.args[0].value).strip()
            if name not in commands:commands.append(name)
            parents=next((k.value for k in n.keywords if k.arg=='parents'),None)
            if isinstance(parents,(ast.List,ast.Tuple)):
                for parent in parents.elts:parent_commands.setdefault(ast.unparse(parent),[]).append(name)
        if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr in {'add_argument_group','add_mutually_exclusive_group'}:
            for target in n.targets:group_parsers[ast.unparse(target)]=ast.unparse(n.value.func.value)
        if not isinstance(n,ast.Call) or not isinstance(n.func,ast.Attribute) or n.func.attr!='add_argument':continue
        names=[literal(a) for a in n.args if isinstance(literal(a),str)]
        if not names or any(x in {'--help','-h'} for x in names):continue
        kw={k.arg:k.value for k in n.keywords}; owner=ast.unparse(n.func.value)
        parser_owner=owner
        while parser_owner in group_parsers:parser_owner=group_parsers[parser_owner]
        dest=literal(kw.get('dest')) or next((x[2:].replace('-','_') for x in names if x.startswith('--')),names[0].lstrip('-').replace('-','_'))
        action=literal(kw.get('action'),'store'); type_node=kw.get('type'); typ=type_node.id if isinstance(type_node,ast.Name) else 'str'
        arguments.append({'_parser_owner':parser_owner,'name':dest,'flags':names,'command':owners.get(owner,''),'positional':not names[0].startswith('-'),'required':bool(literal(kw.get('required'),not names[0].startswith('-') and literal(kw.get('nargs')) not in {'?','*'})),
            'type':typ if typ in {'str','int','float'} else 'str','action':action,'nargs':literal(kw.get('nargs')),'choices':literal(kw.get('choices')),'default':literal(kw.get('default')),
            'default_source':ast.unparse(kw['default']) if 'default' in kw else 'native parser default','path_kind':path_kind(dest),'help':literal(kw.get('help'),'')})
        for g in groups:
            if g['owner']==owner and g.get('command','')==owners.get(owner,''):g['arguments'].append(dest)
    # argparse parent parsers contribute their options only to the commands that
    # inherit them; they are not global/root arguments. Include bare add_parser calls.
    expanded=[]
    for argument in arguments:
        owner=argument.pop('_parser_owner')
        if owner in parent_commands:
            for cmd in parent_commands[owner]:expanded.append({**argument,'command':cmd})
        else:expanded.append(argument)
    # A destination may have separate positive/negative flag actions.
    seen=set()
    for argument in expanded:
        key=(argument['command'],argument['name'])
        if key in seen:
            argument['native_dest']=argument['name']
            argument['name']=next((f[2:].replace('-','_') for f in argument['flags'] if f.startswith('--')),argument['name'])
        seen.add((argument['command'],argument['name']))
    inherited_groups=[]
    for group in groups:
        parser_owner=group_parsers.get(group['owner'],group['owner'])
        while parser_owner in group_parsers:parser_owner=group_parsers[parser_owner]
        for cmd in parent_commands.get(parser_owner,[group['command']]):inherited_groups.append({**group,'command':cmd})
    groups=inherited_groups
    leaves=[c for c in dict.fromkeys(commands) if not any(other.startswith(c+' ') for other in commands)]
    return expanded,leaves,groups,tree

def build(root=None):
    global ROOT
    ROOT=Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    if any(p.is_symlink() for p in ROOT.rglob('*')):raise ValueError('INTERFACE_SOURCE_SYMLINK_FORBIDDEN')
    catalog=json.loads((ROOT/'skill_catalog.json').read_text())['skills']
    dirs={s['id']:(ROOT/s['canonical_path']).resolve() for s in catalog}
    for sid,folder in dirs.items():
        if ROOT not in folder.parents:raise ValueError('INTERFACE_SOURCE_OUTSIDE_ROOT')
        profile=json.loads((folder/'execution_profile.json').read_text())
        for op in profile['operations'].values():
            if op.get('path'):
                target=(dirs.get(op.get('target_skill'),folder)/op['path']).resolve()
                if ROOT not in target.parents:raise ValueError('INTERFACE_SOURCE_OUTSIDE_ROOT')
    resources=json.loads((ROOT/'runtime/resources.json').read_text()); resource_items={**resources.get('model_weights',{}).get('models',{}),**resources.get('runtime_binaries',{})}
    skills=json.loads((ROOT/'skill_catalog.json').read_text())['skills']; dirs={s['id']:ROOT/s['canonical_path'] for s in skills}; result={}
    for s in skills:
        d=dirs[s['id']]; profile=json.loads((d/'execution_profile.json').read_text())
        for op_id,op in profile['operations'].items():
            files={d/'SKILL.md',d/'execution_profile.json',d/'scientific_contract.yaml'}; args=[]; commands=[];groups=[];status='HOST_INSTRUCTIONS'; entry=None; implementation=None
            if op['kind']!='host_authored':
                entry=dirs.get(op.get('target_skill'),d)/op['path']; files.add(entry); implementation=entry
                if op['kind']=='local_python':
                    args,commands,groups,tree=extract(entry)
                    for call in ast.walk(tree):
                        if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='resolve_skill_file' and len(call.args)>=2:
                            sid,rel=literal(call.args[0]),literal(call.args[1])
                            if sid in dirs and isinstance(rel,str):
                                shared=dirs[sid]/rel
                                if shared.is_file():files.add(shared)
                                if not args and shared.suffix=='.py':implementation=shared;args,commands,groups,_=extract(shared)
                    status='STATIC_ARGPARSE' if args else 'INTERFACE_REQUIRES_DECLARATION'
                    if s['id']=='pubmed-database':
                        args=[{'name':'output_file','flags':['output_file'],'command':'','positional':True,'required':True,'type':'str','action':'store','nargs':None,'choices':None,'default':None,'default_source':'required','path_kind':'output','help':'JSON output'}]
                        commands=[]
                        for fn in tree.body:
                            if isinstance(fn,ast.FunctionDef) and not fn.name.startswith('_') and fn.name in re.findall(r'^\s+([a-z_]+),?$',entry.read_text().split('FUNCTIONS = {')[1].split('}')[0],re.M):
                                commands.append(fn.name);defaults=[None]*(len(fn.args.args)-len(fn.args.defaults))+fn.args.defaults
                                for a,de in zip(fn.args.args,defaults):
                                    if a.arg=='self':continue
                                    args.append({'name':a.arg,'flags':['--'+a.arg],'command':fn.name,'positional':False,'required':de is None,'type':a.annotation.id if isinstance(a.annotation,ast.Name) and a.annotation.id in {'int','float','str'} else 'str','action':'store','nargs':None,'choices':None,'default':literal(de),'default_source':'signature','path_kind':'scalar','help':''})
                        status='DECLARED_FUNCTION_CLI'
                else:
                    text=entry.read_text(); flags={}
                    for match in re.finditer(r'getopts\s+["\']([^"\']+)',text):
                        opt=match.group(1);i=0
                        while i<len(opt):
                            ch=opt[i];i+=1;takes=i<len(opt) and opt[i]==':'
                            if takes:i+=1
                            if ch not in {':','h'}:flags['-'+ch]=takes
                    for m in re.finditer(r'(?m)^\s*(-[\w-]+)\)\s*[^\n]*',text):flags[m.group(1)]='\"$2\"' in m.group(0) or '$2' in m.group(0)
                    for flag,takes in flags.items():args.append({'name':flag.lstrip('-').replace('-','_'),'flags':[flag],'command':'','positional':False,'required':False,'type':'str','action':'store' if takes else 'store_true','nargs':None,'choices':None,'default':None,'default_source':'script; inspect instructions','path_kind':'scalar','help':''})
                    status='STATIC_SHELL_FLAGS' if args else 'INTERFACE_REQUIRES_DECLARATION'
            # Explicit non-argparse interfaces and forwarding shell wrapper.
            def declared(name, flag=None, required=False, kind='scalar', action='store'):
                return {'name':name,'flags':[flag or name],'command':'','positional':flag is None,'required':required,'type':'str','action':action,'nargs':None,'choices':None,'default':None,'default_source':'documented native CLI','path_kind':kind,'help':''}
            if s['id']=='literature-review' and op_id in {'generate_pdf','verify_citations'}:
                args=[declared('markdown_file',required=True,kind='input')]
                if op_id=='generate_pdf':args += [declared('output_pdf',required=True,kind='output'),declared('citation_style','--citation-style'),declared('no_toc','--no-toc',action='store_true'),declared('no_numbers','--no-numbers',action='store_true')]
                status='DECLARED_CLI'
            if s['id']=='html-report-renderer':
                implementation=ROOT/'support/html_report_renderer.py';files.add(implementation);args,commands,groups,_=extract(implementation);status='STATIC_ARGPARSE'
            if op_id=='run_cp2k_auto':
                args=[declared(n,f,required=req,kind=kind) for n,f,req,kind in [('input','-i',True,'input'),('output','-o',True,'output'),('workdir','-d',True,'output'),('gpu','-g',False,'scalar'),('threads','-t',False,'scalar')]];status='DECLARED_SHELL_CLI'
            shell_maps={
                'run_cp2k':[('input','-i',True,'input'),('output','-o',True,'output'),('workdir','-d',True,'output'),('ranks','-n',False,'scalar'),('threads','-t',False,'scalar')],
                'run_cp2k_gpu':[('input','-i',True,'input'),('output','-o',True,'output'),('workdir','-d',True,'output'),('gpu','-g',False,'scalar'),('threads','-t',False,'scalar')],
                'cluster_ensemble':[('topology','-p',True,'input'),('trajectory','-x',True,'input'),('tag','-t',False,'scalar'),('clusters','-n',False,'scalar'),('mask','-m',False,'scalar'),('nac','--nac',False,'scalar')],
                'run_scan':[('workdir','-d',True,'output'),('coordinates','-c',True,'input'),('targets','-t',False,'scalar'),('ranks','-n',False,'scalar'),('threads','-T',False,'scalar'),('gpu','-g',False,'scalar'),('template','-m',True,'input'),('start_index','-s',False,'scalar'),('restart_from','-R',False,'input')],
                'param_substrate':[('input','-i',True,'input'),('name','-n',True,'scalar'),('net_charge','-c',True,'scalar'),('multiplicity','-m',False,'scalar'),('output_dir','-o',True,'output')],
                'run_md_ensemble':[('topology','-p',True,'input'),('coordinates','-c',True,'input'),('tag','-t',False,'scalar'),('gpu','-g',False,'scalar'),('restraint_indices','-m',False,'scalar'),('output_dir','-d',True,'output')],
            }
            if op_id in shell_maps:
                args=[declared(n,f,required=req,kind=kind) for n,f,req,kind in shell_maps[op_id]]
                for a in args:
                    if a['name'] in {'ranks','threads','clusters','start_index','net_charge','multiplicity'}:a['type']='int'
                status='DECLARED_SHELL_CLI'
            if op_id=='selfcheck':status='MAINTENANCE_ONLY'
            # Pin all own helper scripts, plus shared implementation helpers and packaged support.
            for folder in {d,implementation.parent.parent if implementation and 'skills' in implementation.relative_to(ROOT).parts else d}:
                files.update(p for p in folder.rglob('*') if p.is_file() and p.suffix in {'.py','.sh','.json','.yaml','.yml','.md','.txt','.pml','.j2','.tmpl','.jinja2','.html','.rst','.toml','.pdb','.cif','.sdf','.smi','.fa','.fasta','.inp','.xml','.dat'} and '__pycache__' not in p.parts)
            files.update(p for p in (ROOT/'support').rglob('*.py') if p.is_file())
            restricted=op_id in {'selfcheck'} or s['id'] in {'uv','workflow-skill-creator'}
            modules=op.get('requires_modules',[])
            if op['kind']=='host_authored':modules={'pymol':['pymol'],'plotly':['plotly'],'seaborn':['seaborn']}.get(s['id'],modules)
            resource_names=op.get('requires_resources',[n for n,item in resource_items.items() if s['id'] in item.get('skills',[])])
            groups=[] if op['kind']=='host_authored' else groups
            resource_groups=[];resource_commands={};required_bins=[]
            if s['id']=='enzyme-qm-ts':
                resource_names={'run_cp2k':['cp2k_cpu'],'run_cp2k_gpu':['cp2k_gpu'],'run_scan':['cp2k_gpu']}.get(op_id,[])
                if op_id=='run_cp2k_auto':resource_groups=[['cp2k_cpu','cp2k_gpu']]
                required_bins={'param_substrate':['obabel','antechamber','parmchk2'],'cluster_ensemble':['cpptraj']}.get(op_id,[])
            if s['id']=='rational-design-advanced':
                resource_names=[]
                if op_id=='external_tools':resource_commands={'plip':['plip'],'caver':['caver','caver_java'],'doctor':[]}
            if s['id']=='pymol' or op['kind']=='host_authored':resource_names=[]
            if s['id']=='alphafold' and op_id in {'local_alphafold','local_colabfold','offline_colabfold'}:
                required_bins=['colabfold_batch']
            # Clarify ambiguous names using their declared types and known native roles.
            for arg in args:
                if arg['choices'] is not None or arg['type'] in {'int','float'} or arg['action'] in {'store_true','store_false'}:arg['path_kind']='scalar'
                if arg['name'] in {'temp_dir','tmp_dir','fulltext_dir'}:arg['path_kind']='output'
                if s['id']=='rational-design-advanced' and op_id=='external_tools' and arg['name']=='config':arg['path_kind']='input'
                if arg['name'] in {'panel_dir','fragments_dir','ens_dir','pretrain_weight','weight'}:arg['path_kind']='input'
                if arg['name'] in {'ligand_params','loops_json','msa_file','fixed_residues_file','design_residues_file','mapping','required_bonds','summary_json'}:arg['path_kind']='input'
                if 'pyrosetta' in s['id'] and arg['name']=='manifest':arg['path_kind']='output'
                if s['id']=='enzyme-qm-ts' and op_id=='analyze_charges' and arg['name'] in {'csv','png'}:arg['path_kind']='output'
                if arg['name'] in {'pdb_id','query_input'}:arg['path_kind']='scalar_or_input'
                if s['id']=='ucsc-conservation-and-tfbs' and arg['name']=='coordinates':arg['path_kind']='scalar'
                if s['id']=='openmm-md' and op_id=='run_openmm_md' and arg['name']=='checkpoint':arg['path_kind']='output'
                if s['id']=='mut-model-supervised-finetuning' and op_id in {'finetune_full','finetune_dpo_full'} and arg['name']=='output_weight':arg['required']=True
            for arg in args:
                semantic=op.get('runtime_contract',{}).get('input_semantics',{}).get(arg['name'])
                if semantic:arg['semantic']=semantic
            env_names=set()
            for source in files:
                if source.suffix!='.py' or 'skills' not in source.relative_to(ROOT).parts:continue
                for call in ast.walk(ast.parse(source.read_text())):
                    if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and ast.unparse(call.func) in {'os.environ.get','os.getenv'} and call.args:
                        name=literal(call.args[0])
                        if isinstance(name,str) and re.fullmatch(r'[A-Z][A-Z0-9_]*',name) and not name.startswith(('CODEX','OPENAI','AWS','AZURE','ENZYME_HTTP')):env_names.add(name)
            result[s['id']+':'+op_id]={'schema_version':'enzyme-core/operation-interface/v1','skill_id':s['id'],'operation':op_id,'kind':op['kind'],'category':s['category'],'description':s['description'],
                'runtime_contract':op.get('runtime_contract',{'status':'UNVERIFIED'}),'arguments':args,'subcommands':commands,'mutually_exclusive_groups':groups,'interface_status':status,'verification':'STATIC_SOURCE_INSPECTION_NOT_RUNTIME_VALIDATION',
                'entrypoint':entry.relative_to(ROOT).as_posix() if entry else None,'implementation':implementation.relative_to(ROOT).as_posix() if implementation else None,
                'source_identity':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
                'effects':{**profile.get('effects',{}),'task_directory_write':profile.get('effects',{}).get('task_directory_write',False) or op['kind']=='host_authored'},'effects_scope':'effective task invocation including Host delivery','requires_modules':modules,'requires_resources':resource_names,'resource_groups':resource_groups,'resource_commands':resource_commands,'requires_binaries':required_bins,'claim_ceiling':profile['evidence_boundary'],
                'policy':'ENGINEERING_ONLY' if s['category']=='testing' else 'EXAMPLE_ONLY' if op_id=='plot_template' else 'MAINTENANCE_ONLY' if restricted else 'SCIENTIFIC','host_modes':['declarative','python'] if op['kind']=='host_authored' and not restricted else ['declarative'] if op['kind']=='host_authored' else [],
                'command_policies':{'valuesranks-key':'EXTERNAL_CREDENTIAL_ISSUANCE','valuesranks-submit':'EXTERNAL_UPLOAD_REQUIRES_EXPLICIT_ADAPTER'} if s['id']=='string-database' else {},
                'environment_variables':sorted(env_names),
                'implementation_arguments':[a['name'] for a in args if a['name'] in {'ligandmpnn_dir','mut_model_dir','venusfsfp_dir'}],
                'credential_arguments':[a['name'] for a in args if a['name'].lower() in {'api_key','token','password','secret','access_token','auth_token'}],
                'calling_limits':['Credential CLI literals forbidden; use documented native environment support, otherwise a credential adapter is required.','Opaque extra-argument strings and generic checkpoint recovery are not permitted.'],
                'credentials':sorted(set({'brenda-database':['BRENDA_EMAIL','BRENDA_PASSWORD','BRENDA_EMIAL'],'pubmed-database':['NCBI_API_KEY','USER_EMAIL','NCBI_TOOL']}.get(s['id'],[])) | {n for f in files if f.suffix=='.py' and 'skills' in f.relative_to(ROOT).parts for n in re.findall(r'[\"\']([A-Z][A-Z0-9_]*(?:API_KEY|PASSWORD|TOKEN|SECRET|EMAIL)[A-Z0-9_]*)[\"\']',f.read_text())}),
                'stdout_artifact':not any(a['path_kind']=='output' for a in args) and op['kind']!='host_authored', 'outputs':'Explicit per-plan artifact contract, checked against operation instructions; output paths are attempt-relative', 'restart_support':'No generic checkpoint recovery'}
    (ROOT/'runtime/operation_interfaces.json').write_text(json.dumps({'schema_version':'enzyme-core/operation-directory/v1','operations':result},ensure_ascii=False,indent=2)+'\n')
    return result

