#!/usr/bin/env python

import commands
import re
import sha  # bleh...when can I assume python >= 2.5?
import sys
from pyparsing import ParserElement, Literal, Optional, Combine, Word, nums, \
                      Regex, ZeroOrMore, OneOrMore, CharsNotIn, \
                      dblQuotedString, \
                      ParseException, ParseSyntaxException

from pyparsing import Token, ParseResults
class ExactData(Token):
  """Specialized pyparsing subclass for handling data dumps in git-fast-import
     exact data format"""
  def __init__( self ):
    super(ExactData,self).__init__()

    self.pattern = r"data (\d+)\n"
    self.re = re.compile(self.pattern)
    self.reString = self.pattern

    self.name = "ExactData"
    self.errmsg = "Expected " + self.name
    self.mayIndexError = False
    self.mayReturnEmpty = True

  def parseImpl( self, instring, loc, doActions=True ):
    result = self.re.match(instring,loc)
    if not result:
      exc = self.myException
      exc.loc = loc
      exc.pstr = instring
      raise exc

    num = result.group(1)
    loc = result.end()+int(num)
    data = instring[result.end():loc]
    d = result.groupdict()
    ret = ParseResults(['data', num, data])
    return loc,ret

  def __str__( self ):
    try:
      return super(ExactMath,self).__str__()
    except:
      pass

    if self.strRepr is None:
      self.strRepr = "Data:"

    return self.strRepr

newmark = 0
mark_dict = {}
def translate_mark(old_mark = None):
  if not old_mark or old_mark not in mark_dict:
    global newmark
    newmark += 1
    mark_dict[old_mark] = newmark

  return mark_dict[old_mark]

class GitElement(object):
  def __init__(self):
    self.type = None

  def dump(self, file):
    raise SystemExit("Unimplemented function: %s.dump()", type(self))

class Blob(GitElement):
  def __init__(self, data, mark = None):
    GitElement.__init__(self)
    self.type = 'blob'
    self.data = data
    self.mark = translate_mark(mark)

  def dump(self, file):
    file.write('blob\n')
    file.write('mark :%d\n' % self.mark)
    file.write('data %d\n%s' % (len(self.data), self.data))
    file.write('\n')

class Reset(GitElement):
  def __init__(self, ref, from_ref = None):
    GitElement.__init__(self)
    self.type = 'reset'
    self.ref = ref
    self.from_ref = from_ref

  def dump(self, file):
    file.write('reset %s\n' % self.ref)
    if self.from_ref:
      file.write('from %s\n' % self.from_ref)
      file.write('\n')

class FileChanges(object):
  def __init__(self, type, filename, mode = None, mark = None):
    self.type = type
    self.filename = filename
    self.mode = mode
    if type == 'M':
      self.mark = translate_mark(mark)

  def dump(self, file):
    if self.type == 'M':
      file.write('M %s :%d %s\n' % (self.mode, self.mark, self.filename))
    elif self.type == 'D':
      file.write('D %s\n' % self.filename)

class Commit(GitElement):
  def __init__(self, branch,
               author_name,    author_email,    author_date,
               committer_name, committer_email, committer_date,
               message,
               file_changes,
               mark = None,
               from_commit = None,
               merge_commits = []):
    GitElement.__init__(self)
    self.type = 'commit'
    self.branch = branch
    self.author_name  = author_name
    self.author_email = author_email
    self.author_date  = author_date
    self.committer_name  = committer_name
    self.committer_email = committer_email
    self.committer_date  = committer_date
    self.message = message
    self.file_changes = file_changes
    self.mark = translate_mark(mark)
    if from_commit:
      self.from_commit = translate_mark(from_commit)
    else:
      self.from_commit = None
    self.merge_commits = [translate_mark(mark) for mark in merge_commits]

  def dump(self, file):
    file.write('commit %s\n' % self.branch)
    file.write('mark :%d\n' % self.mark)
    file.write('author %s <%s> %s\n' % \
                     (self.author_name, self.author_email, self.author_date))
    file.write('committer %s <%s> %s\n' % \
                     (self.committer_name, self.committer_email,
                      self.committer_date))
    file.write('data %d\n%s' % (len(self.message), self.message))
    if self.from_commit:
      file.write('from :%s\n' % self.from_commit)
    for ref in self.merge_commits:
      file.write('merge :%s\n' % ref)
    for change in self.file_changes:
      change.dump(file)
    file.write('\n')

class FastExportParser(object):
  def __init__(self, 
               tag_callback = None,   commit_callback = None,
               blob_callback = None,  progress_callback = None,
               reset_callback = None, checkpoint_callback = None,
               everything_callback = None):
    self._setup_parser()
    self.tag_callback        = tag_callback
    self.blob_callback       = blob_callback
    self.reset_callback      = reset_callback
    self.commit_callback     = commit_callback
    self.progress_callback   = progress_callback
    self.checkpoint_callback = checkpoint_callback
    self.everything_callback = everything_callback

    self.output = sys.stdout

  def _make_blob(self, t):
    # Create the Blob object from the parser tokens
    mark = int(t[1][1:])
    datalen = int(t[3])
    data = t[4]
    if datalen != len(data):
      raise SystemExit('%d != len(%s)' % datalen, data)
    blob = Blob(data, mark)

    # Call any user callback to allow them to modify the blob
    if self.blob_callback:
      self.blob_callback(blob)

    # Now print the resulting blob to stdout
    blob.dump(self.output)

    # Replace data with its sha1sum to cut down on memory usage
    # (python parser stores whole resulting parse tree in memory)
    sha1sum = sha.new(blob.data).hexdigest()
    return ['blob', blob.mark, len(blob.data), sha1sum]

  def _make_reset(self, t):
    # Create the Reset object from the parser tokens
    ref = t[1]
    from_ref = None
    if len(t) > 2:
      from_ref = t[3]
    reset = Reset(ref, from_ref)

    # Call any user callback to allow them to modify the reset
    if self.reset_callback:
      self.reset_callback(reset)

    # Now print the resulting reset to stdout
    reset.dump(self.output)

  def _make_file_changes(self, t):
    if t[0] == 'M':
      mode = t[1]
      mark = int(t[2][1:])
      filename = t[3]
      return FileChanges(t[0], filename, mode, mark)
    elif t[0] == 'D':
      filename = t[1]
      return FileChanges(t[0], filename)

  def _make_commit(self, t):
    #
    # Create the Commit object from the parser tokens...
    #

    # Get the branch
    branch = t[1]
    loc = 2
    tlen = len(t)

    # Get the optional mark
    mark = None
    if t[loc].startswith(':'):
      mark = int(t[loc][1:])
      loc += 1

    # Get the committer; we'll get back to the author in a minute
    offset = (t[loc] == 'author') and loc+4 or loc
    committer_name  = t[offset+1]
    committer_email = t[offset+2]
    committer_date  = t[offset+3]

    # Get the optional author
    if t[loc] == 'author':
      author_name  = t[loc+1]
      author_email = t[loc+2]
      author_date  = t[loc+3]
      loc += 8
    else:
      author_name  = committer_name
      author_email = committer_email
      author_date  = committer_date
      loc += 4

    # Get the commit message
    messagelen = int(t[loc+1])
    message = t[loc+2] # Skip 'data' and len(message)
    if messagelen != len(message):
      raise SystemExit("Commit message's length mismatch; %d != len(%s)" % \
                       messagelen, message)
    loc += 3

    # Get the commit we're supposed to be based on, if other than HEAD
    from_commit = None
    if loc < tlen and t[loc] == 'from':
      from_commit = int(t[loc+1][1:])
      loc += 2

    # Find out if this is a merge commit, and if so what commits other than
    # HEAD are involved
    merge_commits = []
    while loc < tlen and t[loc] == 'merge':
      merge_commits.append( int(t[loc+1][1:]) )
      loc += 2

    # Get file changes
    file_changes = t[loc:]

    # Okay, now we can finally create the Commit object
    commit = Commit(branch,
                    author_name,    author_email,    author_date,
                    committer_name, committer_email, committer_date,
                    message,
                    file_changes,
                    mark,
                    from_commit,
                    merge_commits)

    # Call any user callback to allow them to modify the commit
    if self.commit_callback:
      self.commit_callback(reset)

    # Now print the resulting commit to stdout
    commit.dump(self.output)

  def _setup_parser(self):
    # Basic setup
    ParserElement.setDefaultWhitespaceChars('')
    number = Word(nums)
    lf = Literal('\n').suppress()
    sp = Literal(' ').suppress()

    # Common constructs -- data, ref startpoints
    exact_data = ExactData() + Optional(lf)
    data = exact_data  # FIXME: Should allow delimited_data too
    from_ref  = Literal('from')  + sp + Regex('.*') + lf
    merge_ref = Literal('merge') + sp + Regex('.*') + lf
    person_info = sp + Regex('[^<\n]*(?=[ ])') + sp + \
                  Literal('<').suppress() + Regex('[^<>\n]*') + \
                  Literal('>').suppress() + sp + \
                  Regex('.*') + lf

    # Parsing marks
    idnum = Combine(Literal(':') + number)
    mark = Literal('mark').suppress() - sp + idnum + lf

    # Parsing blobs
    file_content = data
    blob = Literal('blob') + lf + mark + file_content
    blob.setParseAction(lambda t: self._make_blob(t))

    # Parsing branch resets
    reset = Literal('reset') + sp + Regex('.*') + lf + \
            Optional(from_ref) + Optional(lf)
    reset.setParseAction(lambda t: self._make_reset(t))

    # Parsing file changes
    mode = Literal('100644') | Literal('644') | Literal('100755') | \
           Literal('755') | Literal('120000')
    path_str = CharsNotIn(' \n') | dblQuotedString
    file_obm = Literal('M') - sp + mode + sp + idnum + sp + path_str + lf
    file_del = Literal('D') - sp + path_str + lf
    file_change = file_obm | file_del
    #file_change = file_clr|file_del|file_rnm|file_cpy|file_obm|file_inm
    file_change.setParseAction(lambda t: self._make_file_changes(t))

    # Parsing commits
    author_info = Literal('author') + person_info
    committer_info = Literal('committer') + person_info
    commit_msg = data
    commit = Literal('commit') + sp + Regex('.*') + lf + \
             Optional(mark) +                            \
             Optional(author_info) +                     \
             committer_info +                            \
             commit_msg +                                \
             Optional(from_ref) +                        \
             ZeroOrMore(merge_ref) +                     \
             ZeroOrMore(file_change) +                   \
             Optional(lf)
    commit.setParseAction(lambda t: self._make_commit(t))

    # Tying it all together
    cmd = blob | reset | commit
    self.stream = ZeroOrMore(cmd)
    self.stream.parseWithTabs()

  def parse(self, string):
    try:
      results = self.stream.parseString(string, parseAll = True)
    except ParseException, err:
      print err.line
      print " "*(err.column-1) + "^"
      print err
      raise SystemExit
    except ParseSyntaxException, err:
      print err.line
      print " "*(err.column-1) + "^"
      print err
      raise SystemExit
    return results


parser = FastExportParser()
string = commands.getoutput("GIT_DIR=foo/.git git fast-export --all")
results = parser.parse(string)
print results
