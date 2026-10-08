import re, sys, subprocess, html
J='cj.txt'
def get(url):
    return subprocess.run(['curl','-sSL','-m','30','-A','Mozilla/5.0','-c',J,'-b',J,url],capture_output=True,text=True).stdout
def post(url,data):
    args=['curl','-sSL','-m','30','-A','Mozilla/5.0','-c',J,'-b',J,'-e',url,url]
    for k,v in data: args+=['--data-urlencode',f'{k}={v}']
    return subprocess.run(args,capture_output=True,text=True).stdout
u='https://riversidemalta.com.mt/car-hire-insurance'
p=get(u)
hidden=re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"',p)
data=[(k,html.unescape(v)) for k,v in hidden if not k.startswith('UserInputs.Hidden')]
terr=sys.argv[1]; drivers=sys.argv[2] if len(sys.argv)>2 else '1'
data+=[('UserInputs.CountryISO','DE'),('UserInputs.SubProductID','100'),('UserInputs.TerritoryID',terr),('UserInputs.PickUpCountryISO','GR'),('UserInputs.NumberOfDrivers',drivers),('UserInputs.HiddenStartDate','2026-10-15'),('UserInputs.HiddenEndDate','2027-10-14')]
r=post(u,data)
open(f'out_{terr}_{drivers}.html','w').write(r)
t=re.sub(r'<script.*?</script>','',r,flags=re.S); t=re.sub(r'<[^>]+>',' ',t); t=re.sub(r'\s+',' ',html.unescape(t))
for m in re.finditer(r'.{0,120}€.{0,120}',t): print('>>',m.group(0))
