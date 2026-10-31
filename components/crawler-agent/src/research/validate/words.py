"""Words that are written with a capital letter without naming a model.

``subjects`` treats a capitalised word it does not know as a name it does not know ("Mythos", "Command", "Palmyra") -
and a row or a sentence with such a word in front of a value is not taken for the subject's without a person looking.
So the words here are the ones that stand at the beginning of sentences, in table headings and in cells of vendor
pages without being anybody's name.  A word that is missing costs an approval that was not needed; a name that got in
by mistake would be read as an ordinary word - so when in doubt, a word stays out.
"""

from __future__ import annotations

#: Words that are part of somebody's model name however ordinary they are elsewhere: never taken for ordinary words.
#: ("Command R" and "Nova Act" are models; "max" and "pro" are how names end.)
NAME_WORDS = frozenset("""
mini nano micro pro ultra lite flash turbo instant haiku sonnet opus premier sonic reel sol luna astra omni codex instruct embed scout maverick
command diffusion rerank babbage curie ada davinci dall mythos act ray aya vicuna falcon orca dolly zephyr solar cosmos aurora phoenix olympus
robotics realtime transcribe xl coder guard
""".split())

#: ordinary words (English), and the vocabulary of lifecycle, pricing and release pages
_CHOSEN = frozenset("""
a an the this that these those it its they their theirs them we our ours us you your yours he she his her hers who whom whose which what when where why how
all any both each every few fewer many more most much less least no none not only other others another some such same so than then there here thus very too
also just even still yet already again ever never always often sometimes usually now today tomorrow yesterday tonight soon later once twice while until unless
although though because since as if whether either neither nor and or but for from to of in on at by with without within into onto over under above below
between among across through throughout during before after about against along around behind beyond near off out up down per via versus vs upon toward towards
is are was were be been being am do does did done doing have has had having will would shall should can could may might must need needs needed
let lets make makes made making use uses used using see sees seen seeing read reads reading check checks checked note notes noted learn learned visit contact
refer refers follow follows following followed apply applies applied applying include includes included including provide provides provided providing
support supports supported supporting offer offers offered offering allow allows allowed allowing require requires required requiring recommend recommends
recommended suggest suggests suggested announce announces announced announcing release releases released releasing launch launches launched launching
introduce introduces introduced introducing deprecate deprecates deprecated deprecating retire retires retired retiring remove removes removed removing
replace replaces replaced replacing compare compares compared comparing comparison migrate migrates migrated migrating upgrade upgrades upgraded upgrading update updates updated updating
change changes changed changing add adds added adding fix fixes fixed fixing improve improves improved improving reduce reduces reduced reducing
increase increases increased increasing start starts started starting begin begins began begun beginning end ends ended ending continue continues continued
remain remains remained become becomes became move moves moved moving switch switches switched switching transition transitions call calls called calling
send sends sent sending return returns returned receive receives received create creates created creating delete deletes deleted set sets setting settings
get gets getting run runs ran running work works worked working try tries tried want wants know knows known find finds found give gives given take takes taken
keep keeps kept show shows shown showing tell tells told ask asks asked mean means meant expect expected plan plans planned choose choosing select selected
enable enables enabled disable disables disabled configure configured configuring access accessing accessed available unavailable eligible supported unsupported
contact please thanks thank welcome hello yes ok okay ve re ll don doesn didn isn aren wasn weren won wouldn couldn shouldn hasn haven hadn ca wo
model models version versions release date dates time times day days week weeks month months year years quarter hour hours minute minutes second seconds
page pages section sections table tables row rows column columns figure figures notice important warning caution tip tips info information detail details
overview summary introduction background description example examples sample samples guide guides tutorial tutorials documentation docs doc reference
references changelog history news blog post posts article articles announcement announcements faq question questions answer answers help feedback
terms policy policies privacy legal license licensing copyright trademark rights reserved appendix glossary index contents content home menu search
lifecycle legacy eol eos end life deprecation deprecations retirement retirements sunset shutdown shut launch availability generally general preview
public private beta alpha ga experimental stable latest current previous next new old active inactive status state stage phase schedule scheduled upcoming
effective expiry expiration expires expired extended extension replacement replacements successor alternative alternatives migration migrations
pricing price prices priced cost costs rate rates fee fees charge charges charged billing billed bill free paid tier tiers plan standard batch priority flex
provisioned reserved scale throughput demand discount discounts input output inputs outputs token tokens cache cached caching write writes hit hits miss
prompt prompts completion completions request requests response responses context window windows limit limits quota quotas maximum minimum max min total
average avg unit units base long short large small medium size sizes length count number numbers amount amounts region regions regional global zone zones
location locations country countries data residency processing storage memory image images audio video text speech vision multimodal embedding embeddings
fine tuning tuned training inference endpoint endpoints deployment deployments deployed api apis sdk sdks cli ui console portal dashboard account accounts
customer customers user users developer developers team teams organization organizations organisation enterprise business education government partner
partners provider providers vendor vendors platform platforms service services product products feature features capability capabilities tool tools
function functions streaming structured mode modes reasoning thinking effort knowledge cutoff modality modalities language languages code coding
agent agents agentic assistant assistants chat chatbot web browsing file files document documents pdf type types kind name names id ids identifier value values
key keys secret secrets credential credentials permission permissions role roles group groups project projects workspace workspaces environment environments
security secure safety safe compliance compliant audit logging log logs monitoring metrics metric alert alerts incident incidents outage outages issue issues
bug bugs error errors failure failures fault retry retries timeout timeouts latency throughput performance benchmark benchmarks score scores accuracy quality
speed fast faster fastest slow slower best better good great high higher highest low lower lowest early earlier earliest late later full partial complete
limited unlimited optional required additional extra similar different various several certain specific common typical normal default custom native direct
indirect internal external official unofficial original final initial temporary permanent major minor patch breaking backward backwards compatible
compatibility incompatible known unknown open closed pending resolved affected unaffected vulnerable vulnerability vulnerabilities advisory advisories
severity critical moderate medium informational exploit exploited mitigation workaround fixed unfixed published reported disclosed credit credits
currently previously recently originally initially finally typically approximately roughly nearly almost exactly directly automatically manually
january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun am pm noon midnight
one two three four five six seven eight nine ten eleven twelve first second third fourth fifth sixth zero hundred thousand million billion half double triple
mtok tok kb mb gb tb ms sec secs usd eur gbp jpy twd ntd cny krw inr aud cad rpm tpm rpd tpd qps tps utc gmt pst pdt est edt cst cdt mst cet cest jst kst ist
eol ga api sdk us eu uk faq pdf json yaml llm llms ai ml ip id url urls tts stt ocr rag sse vpc iam kms sla slo tpm ptu gpu cpu ram sso mfa pii phi hipaa gdpr
soc iso fedramp tbd tba na ok vs etc ie eg aka asap fyi nb ps re cc strengths weaknesses comparative workaround workarounds cvss cwe reviewed itpm otpm
total subtotal summary result results outcome outcomes impact impacts action actions item items step steps task tasks goal goals scope reason reasons
cause causes effect effects benefit benefits risk risks requirement requirements condition conditions exception exceptions restriction restrictions
option options choice choices alternative method methods approach approaches process processes procedure procedures instruction instructions
recommendation recommendations suggestion suggestions best practice practices consideration considerations
supported unsupported deprecated retired removed replaced added changed fixed improved updated announced launched released extended delayed postponed
cancelled canceled confirmed planned expected estimated tentative provisional subject
input output cached uncached read written per
maximum minimum average median percentile
above below left right top bottom front back inside outside
many several multiple single dual
text image images audio video file files code documents
yes no true false none null unknown unspecified other others misc miscellaneous various
""".split())

#: companies, clouds, their platforms, and places: named on every vendor page, and never "another model"
PLATFORMS = frozenset("""
amazon aws bedrock sagemaker agentcore anthropic openai google gcp vertex deepmind microsoft azure foundry meta cohere nvidia ibm watsonx oracle oci
databricks snowflake alibaba tencent baidu huawei bytedance xai perplexity huggingface hugging face github gitlab pypi npm berriai artificial analysis
lmarena vals infoq reuters bloomberg verge techcrunch arstechnica register zdnet venturebeat ithome apple samsung salesforce sap servicenow stripe
moonshot zhipu minimax stability writer ai21 luma twelvelabs twelve labs together fireworks groq cerebras replicate openrouter
america americas north south east west central northeast southeast northwest southwest europe european asia asian pacific apac emea africa oceania
usa states united kingdom canada mexico brazil chile argentina ireland england france germany switzerland sweden norway finland denmark netherlands
belgium italy spain portugal poland austria israel uae emirates qatar bahrain saudi arabia india japan korea china taiwan singapore malaysia indonesia thailand
vietnam philippines australia zealand hong kong
virginia ohio oregon california texas iowa carolina arizona georgia utah nevada washington york london dublin paris frankfurt zurich stockholm milan madrid
tokyo osaka seoul mumbai hyderabad sydney melbourne jakarta montreal toronto calgary taipei beijing ningxia cape town tel aviv dubai sao paulo
""".split())

#: software, protocols and formats: not models - but when the subject itself is a piece of software (LiteLLM), a line
#: that states a version of one of THESE is not stating the subject's
TECH = frozenset("""
litellm python node nodejs javascript typescript java go golang rust ruby php dotnet kotlin swift scala bash shell powershell
boto3 botocore httpx requests pydantic fastapi flask django uvicorn gunicorn langchain llamaindex langgraph langfuse langsmith datadog prometheus grafana
opentelemetry otel sentry redis postgres postgresql mysql sqlite mongodb dynamodb prisma docker kubernetes helm terraform cloudformation cdk lambda
fargate ecs eks rds aurora cloudwatch cloudfront eventbridge sns sqs amplify gateway nginx envoy istio linux windows macos ubuntu debian alpine ios android
chrome firefox safari edge vscode jupyter slack discord zoom notion jira confluence bitbucket pip uv poetry conda yaml json xml html css http https rest grpc
graphql websocket websockets oauth oidc saml jwt tls ssl ssh tcp udp dns cdn waf vpn mcp a2a sql nosql etl csv markdown latex unicode utf ascii gzip
openapi swagger curl wget git svn cron systemd wsgi asgi cuda pytorch tensorflow onnx transformers tokenizers numpy pandas scipy
""".split())


#: Words that running text writes in lower case: every word that stands at least six times, in lower case and in the
#: middle of a sentence, in the documentation that ships with Python and with this project's dependencies (about 1.5
#: million words of technical English).  A word that is written like that is an ordinary word, also where it begins a
#: sentence or a heading.  Generated once; add to ``_CHOSEN`` above rather than here.
_RUNNING_TEXT = frozenset("""
abandoned abandons abbreviated abbreviation abbreviations abc ability able abort aborted abortion aborts about above abrupt abruptly absence absent
absolute absolutely abstract abstraction abstractions abstractmethods abstracts academic accelerate accelerator accept acceptable acceptance
accepted accepting accepts access accessed accesses accessible accessing accidental accidentally accommodate accompanied accompany accompanying
accord accordance according accordingly account accounting accounts accumulate accumulated accumulates accuracy accurate accurately achieve achieved
ack acknowledge acknowledged acknowledgement acknowledges acknowledgment acquire acquired acquires acquiring across acting action actions activate
activated activating activation active actively activities activity actor acts actual actually ad adapt adaptation adaptations adapted adapter
adapters adaptions adaptive add added addendum adding addition additional additionally additions additive addr address addressed addresses
addressing addrspec adds adequate adhere adjacent adjust adjusted adjustment administered administers admission advance advanced advancing advantage
advantages advertise advertised advertises advertising advice advised affect affected affecting affects affording after afterwards again against
agency agent agents aggregate aggregated aggregating aggregation agree agreeable agreed agreement agreements agrees ahead aid aims aiopg al alert
algebra algorithm algorithms alias aliased aliases aliasing align aligned alignment aligns alive all alla alleged alleging allocate allocated
allocation allocations allow allowable allowed allowing allowlist allows almost alone along alongside alpha alphabet already also alter alterations
altered altering alternate alternately alternating alternative alternatives alters although altogether altri always am ambiguities ambiguity
ambiguous amended among amongst amount amounts an analogous analysis analyze analyzed analyzing ancestor ancestors anche anchor anchored anchors
ancient and android angle angles animation annotate annotated annotating annotation annotations announce announcement annoying anonymous another
answer answered answers anthologies antithetical any anybody anymore anyone anything anytime anyway anyways anywhere apart api app apparatus
apparently appeal appear appearance appeared appears append appended appending appends applicable application applications applied applies apply
applying appreciate appreciated approach appropriate appropriately approval approved approximate approximates approximation apps apt arbitrarily
arbitrary arc arch architecture architectures archival archive archives archiving archs are area arena arenas arg argcomplete argname argparse
argrepr args argument arguments argval arise arising arithmetic arm arms arose around arrange arranged arrangement arrangements arranges array
arrays arrive arrived arrives arriving art article artifact artistic as ascending ascii asdict ask asked asking asks aspects assembled assembler
assent assert asserted asserting assertion assertions assessment assign assigned assigning assignment assignments assigns assimilated assist
assistant associate associated associating assume assumed assumes assuming assumption assumptions assurances assure ast async asynchronous
asynchronously asyncio at atexit atom atomic atomically attach attached attaches attaching attachment attacks attempt attempted attempting attempts
attention attorneys attr attribute attributes attribution attrs audience audio auditing augmentation augmented auth authenticate authenticated
authenticating authentication author authored authoritative authority authorization authorize authorized authorizes authors authorship auto
autodetected automatic automatically autoscaler autospec autouse auxiliary avail availability available average averaged avoid avoidance avoided
avoiding avoids await awaitable awaitables awaited awaiting awaits awakened aware away awful awkward back backed backend backends background
backgroundcolor backgroundimage backing backoff backslash backslashes backtracking backup backward backwards bad badly baggage bail balance balancer
balls bandwidth banner bar bare barf barrier base baseclass based baseline basename bases bash basic basically basis batch batches batching battery
be beamer bear bearer became because become becomes becoming been beer before begin beginning begins begun behalf behave behaves behavior behaviors
behaviour behind being believe believes belong belonging belongs below benchmark benchmarks beneath benefit benefits besides best beta better
between beyond bidirectional big bin binaries binary binascii bind binding bindings binds bins bit bits bitwise blank blanks blob blobs block
blockchain blocked blocking blocks blow bodies body bogus boilerplate bold boldface bondage book bookkeeping bool boolean bootstrap bootstrapped
bootstrapping borrowed both bother botocore bottom bound boundaries boundary bounded bounds box boxes bp bpnumber brace braces bracket bracketing
brackets branch branches branching breach break breakage breakdown breaking breakpoint breakpoints breaks brief bring broadcast broken broker
brought browse browser browsers bubble bucket buckets budget buffer buffered buffering buffers bufsize bug bugs build buildbot buildbots builder
builders building builds built builtin builtins bump bunch bundle bundled bus business busy but button buy by bypass bypassed bypasses byte
bytearray bytecode bytes bytestream bytestring bytestrings bytewise cache cached caches caching cadata cadence cafile calculate calculated
calculates calculating calculation calendar call callable callables callback callbacks called caller callers calling calls came can cancel canceled
cancellation cancelled cancelling cancels candidate candidates cannot canonical canonicalized canvas cap capabilities capability capable capacity
capath capitalize capitalized caps capture captured captures capturing card cardinality care careful carefully cares carriage carried carrier
carries carry carrying case cased casefold cases cash casing caso cast casting catalog catalogue catch catches catching categories category catering
caught cause caused causes causing cbor ccfonts cease ceiling cell cells center central cert certain certainly certfile certificate certificates
certs chain chained chaining chains challenge challenged challenges chance change changed changelog changes changing channel channels chaos char
character characters charbuffer chardet charge charged chars charset chartered charts chat chdir che cheap check checked checker checkers checking
checkpoint checks checksum checksums child childpos children choice choices choose choosing chosen chown chronological chunk chunked chunks
chunksize cinematographic cinematography cipher ciphers circle circuit circular circumstances circumvent circumvented circus citation citations
cited citizen claim claimed claiming claims clash clashes clashing class classes classification classifier classmethod classmethods clause clauses
clean cleaned cleaning cleanly cleans cleanup cleanups clear cleared clearing clearly clears clever cleverly click clicked clicking client clients
clobber clock clone cloning close closed closely closer closes closest closing closure closures cloud cls clue cluster clusters clutter cmd cmdline
coarse code codebase codebases codec codecs coded codename codes coding coefficient coerce coerced colgroup collaborative collapse collapsed collect
collected collecting collection collections collective collector collectors collects collision collisions colon colonmonetary colons color colorama
colored colorful colors column columns combination combinations combine combined combines combining come comes coming comma commaaccent
commaaccentcomb commandline commands commas commence comment commented comments commercial commercially commit commits committed commodo common
commonly commons communicate communicated communicating communication communities community compact companies company comparable compare compared
compares comparing comparison comparisons compat compatibility compatible compelled compensate compensation competition compilation compilations
compile compiled compiler compilers compiles compiling complain complement complete completed completely completeness completer completers completes
completing completion completions complex complexity compliance compliant complicated complication complies comply complying compname component
components composable compose composed composite composition compound comprehension comprehensions comprehensive compress compressed compression
compressor comprise comptype compulsory computation computations compute computed computer computes computing con concatenate concatenated
concatenates concatenating concatenation concept conceptually concern concerned concerning concerns concise concrete concurrency concurrent
concurrently condition conditional conditioned conditions conducts conf confer confidential config configs configurable configuration configurations
configurator configure configured confirmation confirmed conflict conflicting conflicts conform conformance conforming conforms conftest conftests
confuse confused confusing confusion conjectures conjunction connect connected connecting connection connections connectivity connector connects
consecutive consent consented consents consequat consequence consequences consequential conservative consider consideration considerations
considered considering considers consist consistency consistent consistently consisting consists consolations console conspicuously const constant
constants constitute constitutes constituting constrained constrains constraint constraints construct constructed constructing construction
constructor constructors constructs construe construed consult consulted consume consumed consumer consumers consumes consuming consumption contact
contain contained container containers containing containment contains contemplated contemplating content contents contest context contexts
contextual contiguous continuation continuations continue continued continues continuing continuous contract contracted contradiction contrary
contrast contribute contributed contributes contributing contribution contributions contributor contributors contributory control controlled
controller controlling controls convenience convenient convention conventions conversation conversational conversion conversions convert converted
converter converting converts convey conveyed cooked cookie cookiejar cookies cooperate cooperation coordinate coordinates coordinating coordination
coordinator copied copies copy copying copyleft copyreg copyright copyrightable copyrighted copyrights core cores corner coroutine coroutines
corporation correct corrected correction correctly correctness correlation correspond corresponding corresponds corruption cost costs could count
counted counter counterclaim counterparts counters counting countries country counts couple course court courteous courtesy courts covariance
covariant cover coverage covered covering covers cpu crafted crash create created creates creating creation creations creative creator creators
credential credentials credit credits criteria criterion critical cross crypto cryptographic cryptography cte ctx ctypes cui cultural culture
cumulative cure curly current currently curses cursor curve custom customarily customary customer customization customize customized customizing cut
cwd cycle cycles cyclic da daemon daemonic dalla damage damages dance dancers dangerous dangling dark darwin dash dashes data database databases
dataclass dataclasses datagram dataset date datefmt dates datetime day daylight days db dbm de dead deadline deadlines deadlock deadlocks deal
dealing dealings deals death debian debug debugged debugger debugging decide decided decides deciding decimal decision decisions decl declaim
declaration declarations declarative declare declared declaring declined declines decode decoded decoder decoders decodes decoding decompression
decompressor decorate decorated decoration decorator decorators decrease decreasing decrement decremented decrementing dedicate dedicated dedication
deduplicated deemed deep deepcopy deeper deeply def defacto default defaultdict defaulted defaulting defaults defect defective defects defend
defendant defense defensive defer deferred defers define defined defines defining definitely definition definitions definitive deflate degrees dei
del delay delayed delegate delegates delegating delegation delete deleted deletes deleting deletion deletions deliberately delimited delimiter
delimiters deliver delivered delivery della delta deltas demand demarcate denial denies denominator denote density deny deop depend dependence
dependencies dependency dependent depending depends deploying deployment deprecated deprecation depth deque dereferenced derivative derivatives
derive derived derives derogatory descendant descendants descending descent describe described describes describing description descriptions
descriptive descriptor descriptors deserialized design designate designated designed designs desirable desired desktop despite dest destination
destinations destroy destroyed destructor detached detail detailed details detect detected detecting detection detector detectors detects
determination determine determined determines determining deterministic detriment dev developed developer developers development develped devi
deviation device devices devised di diacritical diagnostic diagnostics diagram dialect dialects dialog diameter dict dictionaries dictionary dicts
did die died diff differ difference differencer differences differencing different differentiate differentiates differentiation differently
differing differs difficult difflib diffs digest digests digit digital digitale digitally digits dimension dimensions diminish dir direct direction
directions directive directives directly directories directory directs diritti diritto dirname dirnames dironly dirs dirty disable disabled disables
disabling disallow disallowed disallows disappeared disappears disassemble disassembled disassembly discard discarded discarding discards
discernible disclaim disclaimed disclaimer disclaimers disclaims disclosed disclosure disconnect disconnected disconnects discouraged discover
discoverable discovered discovering discovery discrete discretion discriminator discussing discussion disjoint disk disp dispatch dispatched
dispatcher dispatches dispatching display displayed displaying displaylist displays disputes disrupt dissemination dist distance distinct
distinction distinctions distinguish distinguished distinguishes distinguishing distort distortion distributable distribute distributed distributes
distributing distribution distributions distributor distributors distro distros dists distutils divide dividend division dll do doc docs docstring
docstrings doctest doctests doctrines doctype document documentation documented documenting documents does doing domain domains donated donation
done dot dots dotted double doubled doubles doubling doubt down download downloaded downloading downloads downstream doxygen dpkg draft drafted
drafter drafts drain drained draining drains dramatic draw drawing drawings drawn drift drive driven driver drivers drives drop dropped dropping
drops dst dt du dual duck due dumb dummy dump dumped dunder duplicate duplicated duplicates duplication durable duration durations during duty
dynamic dynamically each eager eagerly earlier earliest early ease easier easiest easily eastward easy echo echoed ed edge edges edit editable
editing editor effect effective effectively effects efficiency efficient efficiently effort efforts eg egg eggs either elaborations elapsed
elasticsearch elect election electronic elem element elements elf elicit elicitation eligible eliminate eliminates eliminating elimination elit
ellipsis else elsewhere email embedded embedding embodied embodiments emission emit emits emitted emitter emitting emoji emptied empty emulate
emulated emulation en enable enabled enables enabling enc encapsulate encapsulates enclosed enclosing encode encoded encoder encoders encodes
encoding encodings encounter encountered encounters encourage encouraged encrypt encrypted encryption end ended endian ending endings endorse
endorsed endorsement endpoint endpoints ends enforce enforceability enforceable enforced enforcement enforces engine engineering engines engraving
enhance enhanced enhancements enjoy enjoyment enormous enough enqueue enqueued enriches ensure ensures ensuring entanglement enter entered entering
enters entertainment entire entirely entirety entities entitled entity entries entropy entry entrypoint entrypoints enum enumerate enumeration
enumerations enums env envelope environ environment environments envvar ephemeral epilogue epoch epoll equal equality equals equitable equivalent
equivalents era errno erroneously error errors escape escaped escapes escaping especially essential essentially est establish established
establishes estimate estimated estimation estoppel et etag etc etex eu eval evaluate evaluated evaluates evaluating evaluation evaluations evaluator
even event events eventually ever every everyone everything everywhere evict evicted eviction evidence evolve exact exactly examine examined
examining example examples exc exceed exceeded exceeds except exception exceptionally exceptions excess exchange exchanged exclamation exclude
excluded excludes excluding exclusion exclusive exclusively exec execing executable executables execute executed executes executing execution
executor exemplar exemplary exempt exemption exercise exercised exercising exhaust exhausted exhaustive exist existed existence existing exists exit
exited exiting exitmsg exits exotic exp expand expanded expanding expands expansion expat expect expectation expectations expected expecting expects
expense expenses expensive experience experiment experimental expiration expire expired expires expiry explain explained explaining explains
explanation explanations explicit explicitly exploit exponent exponential exponentially export exported exporter exporters exporting exports expose
exposed exposes exposing expr express expressed expression expressions expressly exprs extend extended extending extends extensibility extensible
extension extensions extensively extent external externally extra extract extracted extracting extraction extractor extracts extraneous extras
extreme extremely facilitate facilities facility fact factor factories factors factory facts factual fail failed failing failobj fails failure
failures fair fairly faithful fake fall fallback fallbacks falling falls false family fancier fancy far fashion fast faster fastest fatal fault
faulthandler faults favor fd fds feature features fed federal fee feed feedback feeding feel fees fetch fetched fetches fetching few fewer field
fields figure figures figuring file filed filename filenames fileno fileobj filepath files filesystem fill fillcolor filled filling filter filtered
filtering filters final finalization finalize finalized finalizer finalizers finalizes finalizing finally find finder finders finding finds fine
fingerprint finish finished finishes finishing finite fire fired fires firing first fit fitness fits five fix fixations fixed fixer fixers fixes
fixing fixture fixtures flag flags flat flattened flattening flavor flaws flexibility flexible flight float floating floats floor flow flows flush
flushed flushes flushing fmt fn fnmatch focus fold folded folder folding follow followed following follows font fonts foo fooled footer for forbid
forbidden forbids force forced forces forcing foregoing foreign forever forfeit forget forgetting forgotten fork forked forking forkserver form
forma formal format formats formatted formatter formatters formatting formed former formerly forms formula forth forum forward forwarded forwarding
forwards found four fp fraction fractions fragile fragment fragmented fragments frame framerate frames framework frameworkinstall frameworks framing
free freed freedom freely freeware freeze frequency frequently fresh friendly friends from fromlist front frontier frozen frozenset fs fseek ftp
fulfill fulfilled full fullest fullname fully fun func funcname function functional functionality functionally functioning functions functools
funded funky furnished further future futures fuzzy fws gain gamma gap garbage gate gateway gather gathered gave gb gcc gd gdb general generally
generate generated generates generating generation generator generators generic generically generous geography geometric get getattr gets getset
getter getters gettext getting gi git give given gives giving glibc glob global globally globalns globals glue glyph glyphs go goal goals gobble
goes going gone good goodwill got gotten govern governed governing gpl grab grace graceful gracefully grained grammar grammars grant granted
granting grantor grants granular graph graphics gratis great greater greatest greedily greedy greeting grey ground group grouped grouping groups
grow growing grows grpc gsview guarantee guaranteed guarantees guarded guardrail guards guess guessing gui guidance guide guided guidelines gur gzip
hack hacked had half hand handed handing handle handled handler handlers handles handling handoff handshake handy hang hanging happen happened
happening happens happy hard hardcoded harder hardlink hardware harm harmless harmonic has hasattr hash hashable hashes hashing hatch have having
hdrcharset he head header headers headersonly heading health healthy heap heaps heavily heavy height heights heirs held help helped helper helpers
helpful helping helps hence her here hereafter hereby herein hereof hereto hereunder heuristic hex hexadecimal hidden hide hides hiding hierarchical
hierarchy high higher highest highlight highlighted highlighter highlighting highlights highly him hint hinting hints his histogram historic
historical historically history hit hits hitting hog hold holder holders holding holds home homecls honor honored honors hook hooked hooks hope
horizontal host hosted hostname hostnames hosts hot hour hours how however html http httpcore httplib https httpx huge human hurt hybrid hyperlink
hyphen hyphens icon icons id idea ideal idealized ideas idempotency idempotent identical identically identification identified identifier
identifiers identifies identify identifying identities identity idiom idiomatic idle ids ie if iff ignorable ignore ignored ignores ignoring ii il
illegal illustration image images imaginary imaging immediate immediately immunities immutable impact impedance implement implementation
implementations implemented implementer implementing implements implication implications implicit implicitly implied implies imply import importable
important importation imported importer importers importing importlib imports impose imposed impossible impression improve improved improvement
improvements improves improving in inability inaccuracies inaccurate inbound incident incidental include included includes including inclusion
inclusive incoming incompatibilities incompatibility incompatible incomplete inconsistency inconsistent incorporate incorporated incorporates
incorporation incorrect incorrectly increase increased increases increasing increment incremental incrementally incremented incrementing incur
incurred indeed indefinitely indemnify indemnity indent indentation indented indents independent independently index indexed indexes indexing
indicate indicated indicates indicating indication indicator indicators indices indirect indirection indirectly indistinguishable individual
individually individuals induce inducement inducing ineffective ineffectiveness inexact inexpensive infer inference inferred infinite infinities
infinity inflight influence info inform information informational informative informed informing informs infrastructure infringe infringed
infringement infringes inherit inheritance inherited inheriting inherits init initial initialised initialization initialize initialized initializer
initializes initializing initially initiate initiation inject injected injecting injection injects injury inline inlined inner innermost inode
inotify input inputs insecure insensitive insert inserted inserting insertion inserts inside insist inspect inspected inspection inspired install
installation installed installer installing installs instance instances instantiate instantiated instantiating instantiation instead institute
institution institutions instructed instruction instructions instrument instrumentation instrumentations instrumented instrumenting instrumentor
instruments insufficient int intact integer integers integral integrate integrated integrating integration integrity intellectual intelligence
intend intended intending intent intention intentional intentionally interact interacting interaction interactions interactive interactively
intercept interceptor interceptors intercepts interest interested interesting interface interfaces interfere interference interior intermediary
intermediate intern internal internally internals international internationalized internet interpolated interpolation interpret interpretation
interpreted interpreter interpreters interprets interrupt interrupted interruption interrupts intersection interspersed interval intervals
intervention into intraline intrinsic introduce introduced introspect introspection ints invalid invalidate invalidated invalidity invariant inverse
inverted investigate investigated investigation invisible invite invocation invocations invoke invoked invoker invokes invoking involve involved
involves involving io ipaddress ipython irrational irrefutable irrevocable irrevocably is isinstance isjunk isolated isolates isolation issubclass
issue issued issuer issues issuing it italic item items iterable iterables iterate iterated iterating iteration iterator iterators itertools its
itself jar jmespath job jobs joe join joined joining joins joint jointly json judge judged julian jump junk jurisdiction jurisdictions jury just
justification kafka keep keepalive keeping keeps kept kernel kevent key keyboard keychain keyed keyfile keyring keys keyword keywords kick kill
killed kills kind kindly kinds know knowing knowledge known knows kqueue kwarg kwargs kwds kwonlyargs kwonlydefaults la label labeled labeler labels
lack lacks ladder lambda land lands language languages large largely larger largest last late latency latent later latest latter launch launched
launching launchpadlib law lawfully laws lawsuit layer layers layout lazily lazy le lead leading leads leaf leak leakage leaked leaking leaks leap
learn least leave leaves leaving lecture left leftmost leftover legacy legal legally legibly length lengths lenient less lessorequalslant let lets
letter letters letting level levels leveraging lexer lexers lexical liability liable lib libc libffi libidn libm libpng libraries library
libregrtest libs licence licensable license licensed licensee licensees licenses licensing licensor licensors lies lieu life lifecycle lifespan
lifetime light like likelihood likely likeness likewise limit limitation limitations limited limiter limiting limits line linear linebelow
linebelowcomb linecache lineno lines linesep lineterm linguistic link linkage linked linker linking links lira list listed listen listener listeners
listening listens listing lists literal literally literals literary litigation little live lives load loaded loader loaders loading loads loc local
locale localename localhost localization localized locally localns locals localtime locate located locating location locations locator lock locked
locking locks log logged logger loggers logging logic logical logically logicalnot login logo logs lone long longer longest longs look lookahead
lookbehind looked looking looks lookup lookups loop loopback looping loops loose lose losing loss losses lost lot lots low lower lowercase
lowercased lowest lppl lxml lying machine machinery machines macro macroncomb macros made magic magnitude mail mailbox mailboxes mailcap mailing
main mainly maintain maintained maintainer maintainers maintaining maintains maintenance major majority make makefiles makes makesetup making
malformed malfunction malicious manage managed management manager managers manages managing mandatory manifest manipulate manipulated manipulating
manipulation manner manual manually manuals many map mapped mapping mappings maps marginally mark marked marker markers marketing marking markobject
marks markup married marshalling mask masking master match matched matcher matches matching material materials math mathematical matrix matter
matters maxheaderlen maximal maximum maxlinelen maxsize maxsplit may maybe mdotbelow me mean meaning meaningful means meant measure measured
measurement measurements measures measuring mechanical mechanically mechanism mechanisms media median medium meet meeting meets member members
membership memlimit memo memoize memoizing memories memory memoryview mention mentioned menu merchantability mere merely merge merged merges merging
mess message messages messaging messy met meta metaclass metaclasses metadata metavar meter method methods metric metrics microsecond microseconds
middle middleware midnight midpoint midst midyear might migration mild milliseconds mime mimic mimics min mind minidom minimal minimize minimum
minor minted minus minute mirror mirrors miscellaneous mismatch misrepresented miss missed misses missing mistake mistakes mitigate mix mixed mixin
mixing mixture mmap mobile mock mocked mocking mocks mode model modeled models modern modes modification modifications modified modifier modifiers
modifies modify modifying modular module modulefinder modules modulo modulus mojibake moment monetary monitor monitored monitoring monitors monkey
monkeypatching monotonic month months more moreover most mostly motivation mount mouse move moved movement moves moving mro msg msgids msgpack
msvcrt mtime mu much multi multiarch multibyte multicall multicast multiline multipart multiple multiplied multiplying multiprocessing multiset
munged music musical musl musllinux must mutable mutate mutated mutating mutation mutex mutilate mutilation mutual mutually mv my myoff mypy naive
name named namedtuple namedtuples names namespace namespaced namespaces naming nan nargs narrow narrower narrowing nasty national native natural
naturally nature navigate ndiff near nearest nearly necessarily necessary necessitates need needed needing needs negative negligence negotiate
negotiated negotiation neighboring neither nest nested nesting net netloc netmask netrc network networking networks never nevertheless new newer
newest newitem newlib newline newlines newly next nframes nice nicely nicer night no nodeid nodes noise nominated non nonblocking nonce
noncompliance none nonempty nonexclusive nonexistent nonlocal nonnegative nonspecial nonzero nor normal normalised normalization normalize
normalized normalizes normalizing normally not notable notation note noted notes nothing notice noticeable noticed notices notification
notifications notified notify notion now nowhere nth null nullable num number numbered numbering numbers numeral numeric numerical numerically oauth
oauthlib obey obj object objections objects obligated obligation obligations obscure observability observations observe observed observer observes
obsolete obtain obtained obtaining obtains obvious obviously occasional occupied occur occurred occurrence occurrences occurring occurs octal octet
octets odd of off offending offer offered offering offers official offload offloaded offset offsets often ogni ok okay old older oldest omission
omissions omit omits omitted omitting on once one oneof onerror ones onexc ongoing online only onto onward onwards op opaque opcode opcodes open
opened opener opening opentelemetry operand operands operate operates operating operation operations operator operators opportunity opposed opposing
ops opt opted optimal optimization optimizations optimize optimized option optional optionally optionals options optparse opts or orchestrates
orchestrator order ordered ordering orders ordinal ordinals ordinary organization organizations organized orientation oriented origin original
originally originate originated originating originator os other others otherwise otoff ought our ours ourselves out outbound outcome outcomes outer
outermost outgoing outline outlined output outputs outputting outside outstanding over overall overdue overflow overflowing overflows overhead
overlap overlapped overload overloads overridable overridden override overrides overriding oversized overtly overview overwrite overwrites
overwriting overwritten own owned owner owners ownership owning owns package packaged packages packaging packed packet packets packing packrat pad
padded padding page pager pages paginate pagination paginator paging paid painting pair paired pairing pairs paper paradigm paragraph paragraphs
parallel param parameter parameterized parameters parametrization parametrized params paren parenright parent parentheses parenthesis parenthesized
parents parked parse parseable parsed parser parsers parses parsing part partial partialdiff partially participate particular particularly parties
partition partners partnership parts party pass passed passes passing passive password passwords past paste patch patched patches patching patent
patents path pathname pathnames paths pathways pattern patterns pause paused pauses pax pay payload payloads payment payments pdb peer pem pen
pencolor pending pensize people per percent percentage percents perfect perform performance performances performed performer performing performs
perhaps period periodcentered periodic periodically periods perl perlfaq permanent permanently permissible permission permissions permissive
permissively permit permits permitted permitting permyriad perpetual perpetuity persist persisted persistence persistent persisting persists person
personal persons perspective pertain pertaining pertinent pgen pgo phase phases phonogram phonograms photographic photography phrase phrases
physical pick picked picklable pickle pickleable pickled pickles pickling picks picname pid piece pieces pin ping pings pinned pip pipe pipeline
pipes pipx pixels pixmap pkgutil place placed placeholder placeholders placement places placing plain plainly plaintext plan plane planned planning
platform platforms play playback please plist pluggable plugin plugins plural plus pod pods point pointed pointer pointers pointing pointless points
policies policy poll polling polls poly polygon polyitem pong pongs pool pooled pooling pools poor pop popped popping pops popular populate
populated population port portable portal ported porting portion portions ports pos position positional positionals positioning positions positive
positives posix possibilities possibility possible possibly post posting postpone postponed potential potentially power powerful practicable
practical practice practices pre preamble precede preceded precedence precedes preceding precise precisely precision predates predecessor
predecessors predefined predicate predicates predictable preempted preexisting prefer preference preferences preferred prefix prefixed prefixes
prejudicial preloaded prematurely preparation prepare prepared preparing prepend prepended preprocessor prerelease prereleases presence present
presentation presente presented presenting preserve preserved preserves preserving preset presigned pressing presumably presumed pretend pretty
prevailing prevent prevented preventing prevents preview previous previously primarily primary prime priming primitive primitives principal
principally principles print printable printables printed printing prints prior priorities prioritize priority privacy private privileges proactive
probability probable probably probe prober probers probing problem problematic problems procedure procedures proceed proceeding proceeds process
processed processes processing processor processors produce produced producer produces producing product production products profile profiler
profiles profiling profit profits prog program programmatically programmer programming programs progress progressive prohibit prohibited prohibits
project projects proleptic prominent prominently promise promises promote promotional prompt prompting promptly prompts proof propagate propagated
propagates propagating propagation propagator propagators proper properly properties property proposal propose proposed proprietary proprietor
prospective protect protected protecting protection proto protobuf protocol protocols prototype prove provide provided provider providers provides
providing provision provisional provisions proxied proxies proxy pruned pseudo psnfss psutil pty pubblico public publically publication publicity
publicly publish published publisher publishes publishing pull pulled pulls pump punctuation puoi pure purely purpose purposefully purposes pursuant
push pushed pushes put puts putting pyc pydantic pydoc pyexpat pygments pylist pynexttest pyparsing pypi pysqlite pytest qname quadratic qualified
qualifiers qualify quality qualsiasi quantiles quantize queried queries query querying queryparams question questions queue queued queues qui quick
quickly quiet quis quit quite quota quotation quote quoted quotes quotient quoting race races radio radius railroad raise raised raises raising ran
random randomly range ranges rank ranked rapidly rare rarely rate rather ratio rational rationale ratios raw re reach reachable reached reaches
reaching reactive read readability readable reader readers reading readline reads ready real reality realizing really realm realms realname reap
reaped reason reasonable reasonably reasoning reasons reassembles reassign reassigned rebind rebinding rebound rebroadcast rebuild rebuilding
rebuilt recast receive received receiver receivers receives receiving recent recently recipes recipient recipients recitations recognised
recognizably recognize recognized recognizing recommend recommendations recommended recommends recompiled reconnect reconnection reconstruct record
recorded recording recordings records recover recovery recreated recreation recurrence recurse recursing recursion recursive recursively recycled
red redact redacted redaction redefine redefinition redirect redirected redirecting redirection redirections redirector redirects redistributable
redistribute redistributed redistributing redistribution redistributors reduce reduced reduces reducing reduction redundant reentrant ref refactor
refactored refactoring refcycle refer reference referenced references referencing referent referred referring refers reflect reflected reflects
refolded reformed refresh refreshed refreshes refs refuse refused regard regarding regardless regards regex regexes regexp region regions register
registered registering registers registration registries registry regression regular regularly regulated regulation regulations reimplementing
reinitialize reinstated reject rejected rejection rejects relate related relating relation relations relationship relationships relative relatively
relax release released releases relevance relevant reliability reliable reliably relicensing relied relies relinquish reload reloading rely relying
remain remainder remaining remains remarks remedies remedy remember remembers remote removal remove removed removes removing rename renamed renaming
render renderable renderables rendered rendering renders reopen reopened reordered repair reparse repeat repeated repeatedly repeats repertoire
repetition repetitions repl replace replaced replacement replacements replaces replacing replay replayed replica replicate reply repo report
reported reporting reports repositories repository repr represent representable representation representations representative representatives
represented representing represents reprlib reproduce reproduced reproducing reproduction reputation req request requested requesting require
required requirement requirements requires requiring requote reraise rescale rescaling rescission reserved reserves reservoir reset resets resetting
resides resilient resizemode resolution resolve resolved resolvelib resolver resolves resolving resort resource resources respect respecting
respective respectively respects respond responded responds response responses responsibilities responsibility responsible rest restart restarted
restarts restoration restore restored restores restoring restrict restricted restriction restrictions restrictive restricts result resulted
resulting results resume resumed resumes resuming resumption retain retained retaining retains retried retries retrieval retrieve retrieved
retrieves retrieving retry retryable retrying return returncode returned returning returns reusable reuse reused reusing rev reverse reversed revert
reverted review revise revision revisions revisit revocation revoked rewind rewrite rewrites rewritten rfile rich richiesta rid right rightmost
rights risk risks rkcynva rmtree robust role roles rolled rolling rollover romanized room root roots rotate rotated rotating rotation rough roughly
round roundable rounded rounding route routed router routes routine routines routing row rows royalties royalty rrule rule rules ruleset run runner
running runs runtest runtime safe safely safety said sake sale salt same sample sampled sampler samplers samples sampling sampwidth sandbox
sandboxes sane sanitized sanitizer sanity satisfied satisfies satisfy save saved saves saving savings saw say saying says scalar scalars scale
scaled scan scanned scanner scanning scenario scenarios scenes schedule scheduled scheduler schedules scheduling schema schemas scheme schemes
science scientific scope scoped scopes scoping score scores scratch screen screens script scripting scripts scrollable sculpture sdbm sdist se seals
search searched searches searching second secondary seconds secret secrets section sections secure security sed see seed seeds seeing seek seekable
seeking seem seems seen sees segment segments segregated select selectable selected selecting selection selections selector selectors selects self
sell selling semantic semantically semantics semaphore semaphores semconv semicolon semicolons send sender sendfile sending sendmail sends sense
sensible sensitive sensitivity sent sentence sentinel sentinels sep separable separate separated separately separates separating separator
separators sequence sequences sequential serial serializable serialization serialize serialized serializer serializers serializes serializing series
serious sermon serve served server servers serves service serviced services servicing serving session sessions set setattr sets settable setter
setting settings settlement setup setuptools several severed severity shadow shadowed shadowing shall shallow shape shapes share shareable shared
shares sharing she shear shearfactor shebang shell shells shielded shift shifted shim ship shipped short shortcut shorten shorter shortest shorthand
shot should show showed showing shown shows shut shutdown shuts shutting si sia sibling siblings side sides sigma sign signal signaling signals
signature signatures signed signer significant significantly signify signifying signing signs silence silent silently similar similarity similarly
simple simplejson simpler simplest simplicity simplifications simplified simplify simplifying simply simulate simulated simultaneous simultaneously
since sing single singledispatch singleton singletons singular sit site sitecustomize sites sits sitting situation situations six siz size sized
sizes skeleton sketch skill skills skip skipped skipping skips slanted slash slashes slate slave sleep slice slicing sliding slight slightly slot
slots slotted slow slower small smaller smallest smart snapshot snapshots snippet snippets so society sock socket sockets soft software sold sole
solely solution solutions some somehow someone something sometimes somewhat somewhere soon sophisticated sort sortable sorted sorting sorts sought
sound sounds source sourced sources space spaces spam span spans sparse spawn spawned spawning spec special specialized specially specific
specifically specification specifications specified specifier specifiers specifies specify specifying specs speed speeding speeds speedup spelling
spellings spend spent spin spinner split splitext splitlines splits splitting sponsored sponsoring sponsorship spurious sqlcomment sqlcommenter
square src sre ssl stability stable stack stacklevel stage stages stale stamp stamped stand standalone standard standardized standards star
starimports start started starting starts startup stash stat state stated stateful statefulset stateless statement statements states static
statically staticmethod staticmethods stating statistics stats status statute statutory stay stays stderr stdin stdio stdlib stdout steal steering
step stepping steps steward stick still stock stomping stop stoppage stopped stopping stops stopwatch storage store stored stores storing story str
straight straightforward strange strategies strategy stream streamed streaming streams strict stricter strictly string stringification stringified
stringify strings strip stripped stripping strips strong struct structs structural structure structured structures stub stubs stuck studied study
stuff style styles stylesheet styling su sub subclass subclassed subclasses subclassing subcommand subcommands subdir subdirectories subdirectory
subelements subinterpreters subject sublicensable sublicense sublicenseable sublicensed submission submit submits submitted submitting submodule
submodules subnets subnormal subpart subparts subpattern subpatterns subprocess subprocesses subprotocol subprotocols subresources subscribe
subscribed subscriber subscript subscripted subscription subscriptions subsequent subsequently subset subsets substance substantial substantially
substitute substituted substitution substitutions substring substrings subsumed subsystems subtest subtests subtle subtract subtracting subtraction
subtree subtype subtypes subtyping succeed succeeded succeeds success successful successfully successive successor successors such suck sudo suffer
sufficient sufficiently suffix suffixes suggest suggested suggestion suggestions suit suitability suitable suite suites sum summaries summarization
summarize summarizes summary sums super superclass superclasses supersede superseded superset supplemental supplied suppliers supplies supply
supplying support supported supporting supports suppose supposed suppress suppressed sure surface surfaces surname surplus surprising surrendered
surrenders surrogate surrogateescaped surrogates surrounded surrounding survive survives sustained swallow swallowed swap swapped swaps swarm switch
switched switches switching symbol symbolic symbols symlink symlinking symlinks symmetric sync synch synchronization synchronizing synchronous
synchronously syntactic syntactically syntax synthetic sys syscall sysconfig sysctl syslog system systems tab table tables tabs tag tagged tagname
tags tail take taken takes taking tale talk tar tarball target targetpath targets tarinfo task tasks tbody team teardown technical technically
technique technological technology telemetry tell telling tells temp temperature template templates templating tempor temporality temporarily
temporary tends tenets term terminal terminals terminate terminated terminates terminating termination terminator termini terminology termios terms
terrible test testable tested testing testmod tests testsuite texinfo texlive text textbook texts textual tfoot th than thanks that the their them
theme themselves then theory there thereafter thereby therefore therein thereof thereon thereto these they thickness thin thing things think
thinking thinks third thirty this thorough those though thought thousands thread threaded threading threads three threshold throttled throttling
through throughout throughput throw thrown throws thunk thus ticks tie tied ties till time timed timedelta timely timeout timeouts timer times
timespec timestamp timestamps timeval timezone timing title titles tmp tmpdir to together toggle token tokenize tokenizer tokens told tolerance
tolerant tolerate tomfoolery toml too took tool tooling toolkit tools top topdown topic topics toplevel topmost topography topological tops tort
total totally totals touch touched touching tournament toward towards tr trace traceback tracebacks traced tracemalloc tracer traces tracestate
tracing track tracked tracker tracking tracks trade trademark trademarks tradename traditional traffic trailer trailing training transaction
transcript transfer transferable transferring transfers transform transformation transformed transforming transforms transient transition
transitions transitive translate translated translates translating translation translations transliteration transliterations transmission transmit
transmitted transparent transparently transport transports traps traversable traversal traverse traversed traverses traversing treat treated
treating treatment treats treaty tree trees trial trick tricks tricky tried tries trigger triggered triggering triggers trio trip triple triples
triplet trivial true truly truncate truncated truncating truncation trust trusted truth try trying tstr tty tuning tunnel tunneling tunnelling tuple
tuples turn turned turning turns turtle turtles turtleshape tvars tweak twice two type typed typefaces types typescript typesetting typevars typical
typically typing tz tzinfo ugly ultimately umask una unable unaffected unaltered unambiguous unambiguously uname unary unassociated unauthorized
unavailability unavailable unbound unbounded uncatchable uncaught unchanged unclear uncommon uncompressed unconditional unconditionally unconnected
unconstrained undecoded undefined under underflow underlying underscore underscores understand understanding understandings understands understood
undeserialized undesirable undetermined undo undobuffer undocumented undone unencrypted unencumbered unenforceable unequal unequivocal unescaped
unexpected unexpectedly unfair unfamiliar unfinished unfolded unfolding unfortunate unfortunately unhandled unhashable uni unicode unified uniform
uniformly unilaterally uninstall uninstalled unintended union unions unique uniquely unistring unit units unittest universal universally unix
unknown unless unlike unlikely unlimited unlink unlinked unlocked unmaintained unmarshalled unmarshalling unmatched unmodified unnamed unnecessarily
unnecessary unneeded unpack unpacked unpacking unparsable unparseable unparsed unpickle unpickleable unpickled unpickler unpickling unquote unquoted
unquoting unreachable unread unreadable unrecognized unrecoverable unregister unregistered unrelated unreliable unresolved unrestricted unsafe
unserialized unset unsigned unsolicited unspecified unstable unstructured unsubscribe unsubscripted unsuccessful unsupported unsure until untouched
untranslated untrusted unusable unused unusual unwrap unwrapped unzipped up update updated updater updates updating upgrade upgraded upload uploaded
uploading uploads upon upper uppercase upstream uri url urllib urlopen urlparse urls us usability usable usage usages use used useful usegmt useless
user usercustomize userinfo username users uses using ustar usual usually ut utilities utility utilization utilize utilizing utils val valid
validate validated validates validating validation validator validators validity validly valuable value valueless values van var variability
variable variables variance variant variants variation varies variety various vars vary vector vendor vendored venture venue venv verbal verbatim
verbose verbosity verification verified verifier verifies verify verifying versa version versioned versions vertical vertices very vf via vice view
viewed views violate violated violates violating violation violations virtual virtualenv visibility visible visit visited visiting visitor visual
void volume voluntary volunteers vqrn vs vzcyrzragngvba wadllib wait waited waiter waiters waiting waitpid waits waivable waive waived waiver waives
wake wakes waking walk walking wall wallet want wanted wants warn warning warnings warrant warranties warranty was waste watch watchdog watched
watcher watches watching water way ways we weak weakly weakref weakrefs web website websites websocket websockets week weekday weeks weight weighted
weights weird welcome well went were what whatever whatsoever wheel wheels when whence whenever where whereas wherein wherever whether which
whichever while white whitelist whitespace whitespaces who whoever whole wholly whom whose why wide widely widget width widths wild wildcard
wildcards will willing win wind window windows winner wins wire wired wireless wise wish wishes with withdrawn within without witnessing won word
words work workaround worked worker workers workflow workflows working workload works world worldwide worry worse worst worth would wrap wrapped
wrapper wrappers wrapping wraps wrapt writable write writeable writeframesraw writer writers writes writing written wrong wrote xc xdist xe xml
yanked ye year years yet yield yielded yielding yields you your yours yourself zero zeroes zeros zip zipfile zipimport zlib znl zombie zombies zone
zoneinfo zones zsh
""".split())

COMMON = (_CHOSEN | _RUNNING_TEXT) - NAME_WORDS
