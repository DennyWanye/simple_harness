# Retrospective

The real snapshot fixture caught the false assumption that one operation ID has one source
hash; explicit owner joins plus independent supporting facts are necessary. A source
corruption fixture initially hit production append-only guards, so the isolated fixture
now labels its deliberate guard bypass instead of pretending normal writes can corrupt
terminal authority. No expensive Provider/native or repeated whole-suite gate was needed.
