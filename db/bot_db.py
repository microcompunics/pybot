"""
Robot database driven using a Sqlite3 database

Pickle is used internally to serialize the values. 

Author(s): Sudeep Pillai (spillai@csail.mit.edu)
License: MIT

"""
# -*- coding: utf_8 -*-

import os
import re
import sqlite3 as sql
from itertools import izip, imap

try:
    from cPickle import dumps, loads, HIGHEST_PROTOCOL as PICKLE_PROTOCOL
except ImportError:
    from pickle import dumps, loads, HIGHEST_PROTOCOL as PICKLE_PROTOCOL


# def open(*args, **kwargs):
#     """See documentation of the SqliteDict class."""
#     return SqliteDict(*args, **kwargs)

# def encode(obj):
#     """Serialize an object using pickle to a binary format accepted by SQLite."""
#     return sqlite3.Binary(dumps(obj, protocol=PICKLE_PROTOCOL))


# def decode(obj):
#     """Deserialize objects retrieved from SQLite."""
#     return loads(bytes(obj))

def get_scalar(iterable, pos=0): 
    return map(lambda items: items[pos], iterable)

class BotDB(object):
    VALID_FLAGS = ['c', 'r', 'w', 'n']

    def __init__(self, filename=None, flag='c',
                 autocommit=False, journal_mode="DELETE"):
        """
        If no `filename` is given, a random file in temp will be used (and deleted
        from temp once the dict is closed/deleted).

        If you enable `autocommit`, changes will be committed after each operation
        (more inefficient but safer). Otherwise, changes are committed on `self.commit()`,
        `self.clear()` and `self.close()`.

        Set `journal_mode` to 'OFF' if you're experiencing sqlite I/O problems
        or if you need performance and don't care about crash-consistency.

        The `flag` parameter. Exactly one of:
          'c': default mode, open for read/write, creating the db/table if necessary.
          'w': open for r/w, but drop `tablename` contents first (start with empty table)
          'r': open as read-only
          'n': create a new database (erasing any existing tables, not just `tablename`!).

        Note: Setting the text factor to string instead of unicode
        https://docs.python.org/2/library/sqlite3.html#sqlite3.Connection.text_factory
        """
        self.in_temp_ = filename is None
        if self.in_temp_:
            randpart = hex(random.randint(0, 0xffffff))[2:]
            filename = os.path.join(tempfile.gettempdir(), 'sqldict' + randpart)

        if flag not in BotDB.VALID_FLAGS:
            raise RuntimeError("Unrecognized flag: %s" % flag)
        self.flag_ = flag

        if flag == 'n':
            if os.path.exists(filename):
                os.remove(filename)

        dirname = os.path.dirname(filename)
        if dirname:
            if not os.path.exists(dirname):
                raise RuntimeError('Error! The directory does not exist, %s' % dirname)


        self.filename_ = filename
        self.db_ = sql.connect(self.filename_)
        self.db_.text_factory = str

        self.tables_ = {}
        
        # logger.info("opening Sqlite table %r in %s" % (tablename, filename))
        # MAKE_TABLE = 'CREATE TABLE IF NOT EXISTS %s (key TEXT PRIMARY KEY, value BLOB)' % self.tablename
        # self.conn = SqliteMultithread(filename, autocommit=autocommit, journal_mode=journal_mode)
        # self.conn.execute(MAKE_TABLE)
        # self.conn.commit()
        # if flag == 'w':
        #     self.clear()

    def setup(self, cmds): 
        for cmd in cmds.split(';'): 
            if not len(cmd): 
                continue
            print('Setting up: %s %i' % (cmd,len(cmd)))
            
            self.db_.execute(cmd) 
        self.tables_ = { table_name: BotDBTable(self.db_, table_name) 
                         for table_name in self.tables }
        print self.tables
        

    @property
    def tables(self): 
        return list(get_scalar(self.db_.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()))

    def __getitem__(self, tname): 
        return self.tables_[tname]

    def execute(self, req): 
        pass

    def close(self): 
        self.db_.commit()
        self.db_.close()

class BotDBTable(object):
    as_is = lambda item: item
    decoders = {'text': as_is, 'double': as_is, 'integer': as_is,
                'blob': lambda item: loads(bytes(item))} 
    encoders = {'text': as_is, 'double': as_is, 'integer': as_is,
                'blob': lambda item: sql.Binary(dumps(item, -1))}

    def __init__(self, db, name):
        self.db_ = db
        self.name_ = name
        table_info = list(self.db_.execute("PRAGMA table_info('%s')" % self.name_))

        self.fields_ = get_scalar(table_info, pos=1)
        self.dtypes_ = get_scalar(table_info, pos=2)
        self.f2d_ = dict(zip(self.fields_, self.dtypes_))
        print 'F2D: ', self.f2d_

        for dtype in self.dtypes_: 
            if dtype not in BotDBTable.encoders: 
                raise RuntimeError("Cannot encode type: %s" % dtype)
            if dtype not in BotDBTable.decoders: 
                raise RuntimeError("Cannot decode type: %s" % dtype)
                
    def get_dtypes(self, req): 
        """

        Determine dtypes for each input map request, and 
        replace mapped inputs with inputs in the request
        
        Remaps: SELECT from {data} => SELECT from data
        dtypes: {'data': 'blob'} encode/decode type is blob

        """
        
        # Establish inputs and mapped inputs
        inputs = re.findall('\{.*?\}',req)
        mapped_inputs = map(lambda item: item[1:-1], inputs)
        
        try: 
            dtypes = map(lambda item: self.f2d_[item], mapped_inputs)
        except: 
            raise RuntimeError('Unknown decoder for %s, has to be one of %s'
                               % (inputs, self.f2d_))
        
        # Replace mapped inputs with inputs in the request
        req_ = req
        for item,mitem in izip(inputs, mapped_inputs): 
            req_ = req_.replace(item, mitem)
        return req_, dtypes
        
    def retrieve(self, req): 
        req, dtypes = self.get_dtypes(req)

        req_ = req.replace('__TABLE__', self.name_)

        iterable = self.db_.execute(req_)
        return imap(lambda items: 
                    imap(lambda (dtype,item): self.decode(dtype, item), izip(dtypes, items)), 
                    iterable) 

    def update(self, req, iterable): 
        req, dtypes = self.get_dtypes(req)
        print req

        req_ = req.replace('__TABLE__', self.name_)
        
        values = imap(lambda items: 
                      map(lambda (dtype,item): self.encode(dtype, item), 
                          izip(dtypes, items)), 
                      iterable)

        self.db_.executemany(req_, values)
        self.db_.commit()

    def update_inmemory(self, req, iterables, dtype=[]): 
        """
        Data is in the form of tuples fully allocated: 
        values = (np.arange(1000), 
                  [item for item in np.random.randn(1000,200)])
        """
        req_ = req.replace('__TABLE__', self.name_)
        values = izip(* imap(lambda (dtype,iterable): 
             imap(lambda item: self.encode(dtype, item), iterable), 
             izip(dtypes, items)))

        self.db_.executemany(req_, values)
        self.db_.commit()


    def encode(self, dtype, item): 
        if dtype not in BotDBTable.encoders:
            raise RuntimeError("Cannot encode type: %s" % dtype)
        return BotDBTable.encoders[dtype](item)

    def decode(self, dtype, item): 
        if dtype not in BotDBTable.decoders:
            raise RuntimeError("Cannot decode type: %s" % dtype)
        return BotDBTable.decoders[dtype](item)




if __name__ == "__main__": 
    import time
    import numpy as np    
    from bot_utils.itertools_recipes import chunks


    db = BotDB(filename='test.db', flag='w')
    db.setup(
        '''CREATE TABLE IF NOT EXISTS channels (name text, id integer, length integer);'''
        '''CREATE TABLE IF NOT EXISTS sensor_1 (timestamp double, data blob);'''
        '''CREATE TABLE IF NOT EXISTS sensor_2 (timestamp double, data blob);'''
    )
    
    # Insert large number of values
    # values = izip(np.arange(1000), [item for item in np.random.randn(1000,200)])    
    def random_data_generator(): 
        for j in range(1000000): 
            yield (j, np.random.randn(10000))
    values = random_data_generator()

    st = time.time()
    for idx, chunk_values in enumerate(chunks(values, 1000)): 
        print('{:03}'.format(idx))
        db['sensor_1'].update('''INSERT INTO __TABLE__ '''
                              '''({timestamp}, {data}) VALUES (?,?)''', chunk_values)

    print('Time taken: {:4.3f} s'.format(time.time() - st))

    res = db['sensor_1'].retrieve(
        '''SELECT {timestamp}, {data} FROM __TABLE__ '''
        '''WHERE timestamp > 10 AND timestamp < 15'''
    )
    for r in res: 
        t,d = r 
        print t,d.shape
    db.close()

