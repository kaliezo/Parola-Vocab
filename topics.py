"""Study topics inferred from a card's meaning, without changing saved data."""

import re
from functools import lru_cache


ALL_TOPICS = "All topics"
TOPICS = (
    "Food and drink", "Colors and shapes", "Animals", "Nature and weather",
    "Body and health", "People and relationships", "Home and daily life",
    "Clothing and appearance", "Travel and transport", "Places and buildings",
    "Work and money", "School and learning", "Communication and language",
    "Technology and media", "Arts and entertainment", "Sports and leisure",
    "Time and numbers", "Feelings and personality", "Society and law",
    "Science and space", "Objects and materials", "Ideas and qualities",
    "Actions", "Descriptions", "Expressions", "General vocabulary",
)

# Match specific meanings in the English gloss. Broad words such as "thing",
# "place", and "person" are deliberately omitted because they occur in many
# definitions without describing the card's topic.
KEYWORDS = {
    "Food and drink": """
        apple apricot artichoke avocado bacon banana bean beef beer berry bread
        breakfast broccoli butter cabbage cake candy carrot cereal cheese cherry
        chickpea chicken chocolate coffee cookie corn cream cucumber dessert dinner dough
        doughnut drink egg fish flour food fruit garlic gin grape honey jam juice
        lemon lettuce lunch meat meal milk mushroom olive onion orange pasta
        peach pear pepper pie pizza potato rice salad salt sandwich sauce
        sausage soup spinach steak strawberry sugar tea tomato vegetable vinegar
        water wine yogurt ravioli gnocchi lasagna risotto mozzarella prosciutto
        espresso cappuccino biscuit croissant pastry breadstick oliveoil
        eat cook bake boil fry taste flavor hungry thirst sip restaurant
        café cafe kitchen recipe ingredient dish dining wheat pea lentil
        oatmeal porridge seafood mussel shrimp prawn tuna salmon cod
        parsley basil celery zucchini courgette eggplant aubergine gelato
    """,
    "Colors and shapes": """
        black blue brown color colour colorful dark green grey gray orange pink
        purple red violet white yellow turquoise beige scarlet crimson shape
        circle circular oval round square rectangle rectangular triangle
        triangular cube curve curved straight spiral diagonal horizontal
        vertical stripe striped dot dotted
    """,
    "Animals": """
        animal ant bear bee bird boar butterfly camel cat chicken cow crocodile
        deer dog dolphin donkey duck eagle elephant fish fox frog goat goose
        horse insect lion lizard monkey mouse octopus owl pig rabbit rat reptile
        shark sheep snake spider squirrel tiger turtle whale wolf worm zoo pet
        puppy kitten feather paw hoof tail wing beak
    """,
    "Nature and weather": """
        nature weather rain rainy snow snowy wind windy storm thunder lightning
        cloud cloudy sun sunny moon sky climate temperature season autumn
        spring summer winter tree forest wood leaf flower rose plant grass garden
        mountain hill valley river lake sea ocean beach island desert jungle
        earth soil rock stone volcano waterfall field countryside farm
        cosmos universe planet eclipse orbit galaxy astronomy altitude gulf bay
    """,
    "Body and health": """
        abdomen ankle arm blood bone brain breast chest chin ear elbow eye
        finger foot hair hand head heart hip knee leg lip lung mouth muscle
        neck nose rib shoulder skin stomach throat tongue tooth teeth waist
        wound injury pain ache illness disease fever cough cold flu cancer
        malaria medicine medical doctor nurse hospital clinic surgery vaccine
        pharmaceutical treatment patient health healthy sick heal cure aids hiv
        itch ulcer sore skeleton therapist midwife drip cranial scalp nape
        dentist pregnancy pregnant virus uterus womb
    """,
    "People and relationships": """
        baby boy brother child children cousin daughter family father female
        friend friendship girl grandfather grandmother husband lover male man
        marriage married mother neighbor nephew niece parent partner relative
        sister son uncle wife woman adult teenager boyfriend girlfriend
        colleague coworker guest host stranger couple relationship kiss wedding
        stepmother stepfather godmother godfather bride bridesmaid childhood
    """,
    "Home and daily life": """
        home house apartment bedroom bathroom kitchen livingroom room bed chair
        couch sofa table desk door window wall floor ceiling roof staircase
        furniture cupboard cabinet shelf drawer lamp mirror pillow
        blanket towel soap shower bath sink toilet refrigerator fridge oven
        stove washing laundry clean cleaning broom mop basket household
        key lock garage balcony mattress carpet curtain
    """,
    "Clothing and appearance": """
        appearance beautiful handsome ugly dress shirt skirt trousers pants
        jeans shorts shoe boot sock coat jacket sweater scarf glove hat cap
        belt tie suit uniform costume clothing clothes garment wear fashion
        makeup lipstick perfume necklace ring bracelet earring haircut beard
        moustache blonde brunette hairstyle
    """,
    "Travel and transport": """
        travel trip journey vacation holiday tourist tourism passport ticket
        luggage suitcase baggage airport airplane plane aircraft helicopter
        tram subway metro bus taxi car bicycle bike motorcycle scooter
        truck van boat ship ferry yacht vehicle traffic road highway railway
        station platform driver passenger pilot sailor transport commute
        depart departure arrive arrival destination route ride drive fly sail
        hitchhiking pier dock transfer relocation
    """,
    "Places and buildings": """
        city town village country neighborhood district street avenue square
        bridge tunnel building palace castle tower church cathedral mosque
        temple museum library cinema theater theatre stadium park playground
        hotel hostel inn bank postoffice office factory warehouse shop store
        supermarket market pharmacy school university airport harbor port
        hospital prison courthouse restaurant café cafe alley elevator lift
    """,
    "Work and money": """
        work job career profession employee employer worker salary wage
        income money cash coin bank budget price cost expensive cheap
        buy sell purchase sale shopping shop store market merchant trade
        business company firm industry factory manager boss customer client
        contract hire employment retire pension tax debt loan profit
        invoice bill receipt wallet euro dollar cent penny economy economic
        discount bankruptcy auction acquisition mortgage specialist official
    """,
    "School and learning": """
        school university college student pupil teacher professor classroom
        lesson class lecture exam test quiz homework study learn teach
        education educational knowledge textbook notebook reading write
        writing pencil pen paper alphabet grammar mathematics math
        research diploma degree graduation scholarship library assessment
    """,
    "Communication and language": """
        language word letter sentence speech speak talk say tell ask answer
        reply conversation discussion message email mail call telephone phone
        listen hear read write translate translation interpreter pronounce
        pronunciation accent vocabulary idiom phrase expression grammar
        meaning definition question communicate communication whisper shout
        announce explain describe argue debate interview publish publisher
    """,
    "Technology and media": """
        computer laptop smartphone mobile internet web website online network
        software hardware application app program programming code digital
        data database file folder screen keyboard mouse printer camera video
        television tv radio podcast film movie photograph photo image
        electronic electricity electrical circuit battery cable charger
        device machine robot satellite technology technological media news
        newspaper magazine broadcast tweet
    """,
    "Arts and entertainment": """
        art artist artistic painting painter picture sculpture sculptor museum
        music musical song sing singer piano guitar violin orchestra concert
        dance dancer theater theatre cinema film movie actor actress drama
        comedy performance show spectacle festival poetry poem poet novel
        literature literary story fairy tale drawing design photography
    """,
    "Sports and leisure": """
        sport athletic athletics football soccer basketball baseball tennis
        volleyball rugby golf swimming swim run running race racing cycling
        bicycle chess game play player team coach athlete champion goal
        tournament competition compete exercise gym fitness yoga hobby
        hiking camping fishing skiing skating skateboard bowling poker
        leisure recreation holiday vacation
    """,
    "Time and numbers": """
        time hour minute second day week month year decade century morning
        afternoon evening night today tomorrow yesterday calendar date clock
        noon midnight early late soon later former future past age number
        numeral zero hundred thousand million first previous twice double
        half quarter percent percentage count counting length weight fifth
    """,
    "Feelings and personality": """
        emotion feeling love hate anger angry joy happy happiness sad sadness
        fear afraid anxiety anxious worry nervous calm brave courage shy
        embarrassment embarrassed shame pride proud regret sorrow grief
        trust hope hopeful despair surprise surprised jealous jealousy
        kindness kind cruel cruelty generous selfish friendly lonely
        confidence confident patient impatient attitude mood character
        personality enthusiasm devotion affection passion
    """,
    "Society and law": """
        government nation national parliament president minister mayor
        politician political politics vote election democracy republic
        citizen citizenship public society social community culture religion
        religious church law legal illegal court judge lawyer trial jury
        police officer crime criminal murder theft violence prison arrest
        punishment rights duty freedom justice war army soldier military
        peace treaty border immigrant immigration federal injunction electoral
    """,
    "Science and space": """
        science scientific research experiment laboratory physics chemistry
        biology geology astronomy atom molecule particle element chemical
        energy gravity magnet magnetic electric electricity solar cosmic
        cosmos universe galaxy planet orbit satellite eclipse solar
        theory theorem formula hypothesis discovery invention evolution
    """,
    "Objects and materials": """
        object item tool instrument equipment gear hammer knife sword blade
        handle wheel key lock chain nail needle wire beam board pole stick
        pipe tube filter magnet bottle container box bag basket bowl cup
        glass jar rope thread fabric cloth cotton wool leather plastic
        metal iron copper gold silver steel bronze brick clay fiber fibre
        paper cardboard rubber wood material substance powder liquid solid
    """,
    "Ideas and qualities": """
        idea concept thought truth fact reason cause result effect method
        process system rule principle possibility probability chance choice
        decision purpose goal need problem solution value quality quantity
        difference similarity change progress development condition situation
        circumstance event case issue importance relevance order structure
        category type kind level balance clarity risk opportunity benefit
        requirement consequence exception alternative approach explanation
    """,
}

ITALIAN_SIGNALS = {
    "Food and drink": ("cibo", "bevanda", "alimento", "piatto", "mangiare", "bere"),
    "Colors and shapes": ("colore", "tonalità", "forma geometrica"),
    "Animals": ("animale", "uccello", "insetto", "mammifero", "rettile"),
    "Nature and weather": ("pianta", "albero", "fenomeno atmosferico", "tempo atmosferico"),
    "Body and health": ("parte del corpo", "malattia", "sintomo", "medicina", "salute"),
    "People and relationships": ("persona della famiglia", "parente", "rapporto familiare"),
    "Clothing and appearance": ("indumento", "capo di abbigliamento", "vestito"),
    "Travel and transport": ("mezzo di trasporto", "viaggio", "veicolo"),
    "School and learning": ("scuola", "insegnamento", "studente"),
    "Technology and media": ("dispositivo elettronico", "computer", "internet"),
    "Arts and entertainment": ("strumento musicale", "opera d'arte", "spettacolo"),
    "Sports and leisure": ("sport", "gioco da tavolo"),
    "Time and numbers": ("unità di tempo", "numero", "periodo di tempo"),
    "Feelings and personality": ("sentimento", "emozione", "stato d'animo"),
    "Society and law": ("legge", "governo", "reato", "società"),
    "Science and space": ("scienza", "esperimento", "pianeta", "corpo celeste"),
    "Objects and materials": ("oggetto", "strumento", "materiale", "sostanza"),
    "Ideas and qualities": ("idea", "concetto", "qualità", "possibilità"),
}

KEYWORD_SETS = {topic: frozenset(words.split()) for topic, words in KEYWORDS.items()}
GLOSS_WORDS = re.compile(r"[a-z]+")
NUMBER_WORDS = frozenset("one two three four five six seven eight nine ten eleven twelve thirteen "
                         "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty "
                         "fifty sixty seventy eighty ninety".split())
EXACT_TOPICS = {
    ("arancia", "orange"): ("Food and drink",),
    ("arancione", "orange"): ("Colors and shapes",),
    ("mandarino", "mandarin orange"): ("Food and drink",),
    ("piazza", "square"): ("Places and buildings",),
    ("cerchia", "circle"): ("People and relationships",),
    ("giostra", "merry-go-round"): ("Sports and leisure",),
    ("dentifricio", "toothpaste"): ("Body and health", "Home and daily life"),
    ("vomito", "vomit"): ("Body and health",),
    ("mouse", "computer mouse"): ("Technology and media",),
    ("treno", "train"): ("Travel and transport",),
    ("allenarsi", "to train"): ("Sports and leisure",),
    ("fantascienza", "science fiction"): ("Arts and entertainment",),
    ("pescare", "to fish"): ("Sports and leisure",),
    ("fiammifero", "match"): ("Objects and materials",),
    ("rock", "rock music"): ("Arts and entertainment",),
    ("bosco", "wood"): ("Nature and weather",),
    ("sale", "salt"): ("Food and drink",),
    ("dritto", "straight ahead"): ("Descriptions",),
    ("moro", "dark-haired"): ("Clothing and appearance",),
    ("negro", "black person (dated, often offensive term)"): ("General vocabulary",),
}


def _word_forms(gloss):
    tokens = set(GLOSS_WORDS.findall(gloss.casefold()))
    forms = set(tokens)
    for token in tokens:
        if token.endswith("ies") and len(token) > 4:
            forms.add(token[:-3] + "y")
        elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            forms.add(token[:-1])
    return forms


def _definition_head(definition):
    head = definition.casefold().strip()
    for article in ("una ", "uno ", "un ", "il ", "la ", "lo ", "l'"):
        if head.startswith(article):
            return head[len(article):]
    return head


@lru_cache(maxsize=20000)
def topics_for_text(word, gloss, definition, kind="word"):
    """Return one or more broad topics for an Italian card."""
    expression = kind in ("phrase", "idiom", "proverb") or " " in word
    if expression:
        return ("Expressions",)
    primary_gloss = (gloss or "").split(";", 1)[0].strip()
    exact = EXACT_TOPICS.get((word.casefold(), primary_gloss.casefold()))
    if exact:
        return exact
    words = _word_forms(primary_gloss)
    head = _definition_head(definition or "")
    matches = []
    for topic in TOPICS:
        if topic in KEYWORD_SETS and words & KEYWORD_SETS[topic]:
            matches.append(topic)
        elif topic in ITALIAN_SIGNALS and any(head.startswith(signal) for signal in ITALIAN_SIGNALS[topic]):
            matches.append(topic)
    first_gloss = primary_gloss.casefold()
    if first_gloss in NUMBER_WORDS and "Time and numbers" not in matches:
        matches.append("Time and numbers")
    if matches:
        return tuple(matches)
    if first_gloss.startswith("to "):
        return ("Actions",)
    if word.casefold().endswith("mente") or head.startswith(("che ", "relativo a ")):
        return ("Descriptions",)
    first_word = next(iter(GLOSS_WORDS.findall(first_gloss)), "")
    if first_word.endswith(("tion", "sion", "ness", "ity", "ment", "ship", "ance", "ence", "ism")):
        return ("Ideas and qualities",)
    return ("General vocabulary",)


def topics_for_card(card):
    return topics_for_text(card["original_text"], card["gloss_en"] or "",
                           card["definition_it"] or "", card["starter_kind"] or "word")
